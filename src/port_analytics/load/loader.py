"""Talks to Azure SQL: applies the schema, then upserts ports, cargo
types, throughput rows, and flags via the MERGE statements built in
upsert.py. Thin by design -- the SQL itself lives in upsert.py where
it's unit-testable without a database; this module is verified by a
real run against the actual Azure SQL free-tier database (see README),
not by heavy unit testing against a fake connection.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pyodbc

from port_analytics.load.upsert import (
    build_cargo_type_upsert,
    build_flag_upsert,
    build_port_merge_link,
    build_port_upsert,
    build_throughput_upsert,
)
from port_analytics.models import DataQualityFlag, FlagType, PortThroughputRow
from port_analytics.transform.reference_data import CARGO_TYPES, PORTS

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

ThroughputKey = tuple[str, str, int, str]


class LoadSummary(NamedTuple):
    ports_loaded: int
    cargo_types_loaded: int
    throughput_rows_loaded: int
    flags_loaded: int
    revisions_detected: int


def apply_schema(conn: pyodbc.Connection) -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    cursor = conn.cursor()
    cursor.execute(sql)
    conn.commit()


def upsert_ports(conn: pyodbc.Connection) -> dict[str, int]:
    cursor = conn.cursor()
    port_ids: dict[str, int] = {}
    for port in PORTS.values():
        stmt = build_port_upsert(port)
        cursor.execute(stmt.sql, stmt.params)
        row = cursor.fetchone()
        port_ids[port.eurostat_code] = row.port_id

    for port in PORTS.values():
        if port.merged_into is not None:
            link = build_port_merge_link(port.eurostat_code, port_ids[port.merged_into])
            cursor.execute(link.sql, link.params)

    conn.commit()
    return port_ids


def upsert_cargo_types(conn: pyodbc.Connection) -> dict[str, int]:
    cursor = conn.cursor()
    cargo_type_ids: dict[str, int] = {}
    for cargo_type in CARGO_TYPES.values():
        stmt = build_cargo_type_upsert(cargo_type)
        cursor.execute(stmt.sql, stmt.params)
        row = cursor.fetchone()
        cargo_type_ids[cargo_type.cargo_type_code] = row.cargo_type_id
    conn.commit()
    return cargo_type_ids


def upsert_throughput_rows(
    conn: pyodbc.Connection,
    rows: list[PortThroughputRow],
    port_ids: dict[str, int],
    cargo_type_ids: dict[str, int],
) -> tuple[dict[ThroughputKey, int], list[DataQualityFlag]]:
    """Returns the (port_code, cargo_type_code, year, direction) ->
    throughput_id map, and revised_estimate flags for any row whose
    value changed from a prior run -- this is what closes the loop
    Phase 2 left open (a single pull can't detect revisions; comparing
    against what's already loaded can)."""
    cursor = conn.cursor()
    throughput_ids: dict[ThroughputKey, int] = {}
    revision_flags: list[DataQualityFlag] = []

    for row in rows:
        port_id = port_ids[row.port_code]
        cargo_type_id = cargo_type_ids[row.cargo_type_code]
        stmt = build_throughput_upsert(row, port_id, cargo_type_id)
        cursor.execute(stmt.sql, stmt.params)
        action, throughput_id, old_value, new_value = cursor.fetchone()
        key = (row.port_code, row.cargo_type_code, row.year, row.direction.value)
        throughput_ids[key] = throughput_id

        value_changed = old_value is not None and abs(float(old_value) - float(new_value)) > 0.01
        if action == "UPDATE" and value_changed:
            port_name = PORTS[row.port_code].port_name
            cargo_name = CARGO_TYPES[row.cargo_type_code].cargo_type_name
            revision_flags.append(
                DataQualityFlag(
                    flag_type=FlagType.REVISED_ESTIMATE,
                    port_code=row.port_code,
                    description=(
                        f"{port_name} {cargo_name} ({row.direction.value}) {row.year} changed from "
                        f"{old_value} to {new_value} tonnes between pipeline runs "
                        f"(source={row.source})."
                    ),
                    resolution=(
                        "Latest value applied; the prior value was overwritten, not retained "
                        "separately."
                    ),
                )
            )

    conn.commit()
    return throughput_ids, revision_flags


def upsert_flags(
    conn: pyodbc.Connection,
    flags: list[DataQualityFlag],
    port_ids: dict[str, int],
    throughput_ids: dict[ThroughputKey, int],
) -> None:
    cursor = conn.cursor()
    for flag in flags:
        port_id = port_ids[flag.port_code] if flag.port_code else None
        throughput_id = None
        if flag.throughput_ref is not None:
            key: ThroughputKey = (
                flag.throughput_ref.port_code,
                flag.throughput_ref.cargo_type_code,
                flag.throughput_ref.year,
                flag.throughput_ref.direction.value,
            )
            # None if the referenced row doesn't exist -- the common
            # case, since these flags describe missing data by definition.
            throughput_id = throughput_ids.get(key)
        stmt = build_flag_upsert(flag, port_id, throughput_id)
        cursor.execute(stmt.sql, stmt.params)
    conn.commit()


def load_all(
    conn: pyodbc.Connection,
    rows: list[PortThroughputRow],
    flags: list[DataQualityFlag],
) -> LoadSummary:
    apply_schema(conn)
    port_ids = upsert_ports(conn)
    cargo_type_ids = upsert_cargo_types(conn)
    throughput_ids, revision_flags = upsert_throughput_rows(conn, rows, port_ids, cargo_type_ids)
    all_flags = flags + revision_flags
    upsert_flags(conn, all_flags, port_ids, throughput_ids)

    return LoadSummary(
        ports_loaded=len(port_ids),
        cargo_types_loaded=len(cargo_type_ids),
        throughput_rows_loaded=len(rows),
        flags_loaded=len(all_flags),
        revisions_detected=len(revision_flags),
    )
