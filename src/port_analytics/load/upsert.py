"""Pure SQL-statement builders for idempotent loading. No database
connection anywhere in this module -- each function takes a domain
object and returns the exact MERGE/UPDATE text plus its parameter tuple,
so the SQL itself is unit-testable without Azure SQL.

Every statement uses `?`-style parameter placeholders (pyodbc's paramstyle)
and MERGEs on each table's natural key rather than blindly inserting, so
re-running the loader against unchanged input never duplicates rows —
that's what makes the pipeline idempotent per the spec's Phase 3
requirement.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from port_analytics.models import CargoType, DataQualityFlag, Port, PortThroughputRow


class Statement(NamedTuple):
    sql: str
    params: tuple[Any, ...]


def build_port_upsert(port: Port) -> Statement:
    """Upserts on eurostat_code. merged_into_port_id is deliberately not
    set here -- it's a self-referencing FK that can only be resolved
    once every port has a real port_id, so it's a separate pass
    (build_port_merge_link) run after every port is upserted."""
    sql = """
        MERGE dbo.ports AS target
        USING (SELECT ? AS eurostat_code) AS src
        ON target.eurostat_code = src.eurostat_code
        WHEN MATCHED THEN
            UPDATE SET port_name = ?, country_code = ?, un_locode = ?
        WHEN NOT MATCHED THEN
            INSERT (port_name, country_code, un_locode, eurostat_code)
            VALUES (?, ?, ?, ?)
        OUTPUT inserted.port_id, inserted.eurostat_code;
    """
    params = (
        port.eurostat_code,
        port.port_name,
        port.country_code,
        port.un_locode,
        port.port_name,
        port.country_code,
        port.un_locode,
        port.eurostat_code,
    )
    return Statement(sql=sql, params=params)


def build_port_merge_link(eurostat_code: str, merged_into_port_id: int) -> Statement:
    sql = "UPDATE dbo.ports SET merged_into_port_id = ? WHERE eurostat_code = ?;"
    return Statement(sql=sql, params=(merged_into_port_id, eurostat_code))


def build_cargo_type_upsert(cargo_type: CargoType) -> Statement:
    sql = """
        MERGE dbo.cargo_types AS target
        USING (SELECT ? AS cargo_type_code) AS src
        ON target.cargo_type_code = src.cargo_type_code
        WHEN MATCHED THEN
            UPDATE SET cargo_type_name = ?
        WHEN NOT MATCHED THEN
            INSERT (cargo_type_name, cargo_type_code)
            VALUES (?, ?)
        OUTPUT inserted.cargo_type_id, inserted.cargo_type_code;
    """
    params = (
        cargo_type.cargo_type_code,
        cargo_type.cargo_type_name,
        cargo_type.cargo_type_name,
        cargo_type.cargo_type_code,
    )
    return Statement(sql=sql, params=params)


def build_throughput_upsert(row: PortThroughputRow, port_id: int, cargo_type_id: int) -> Statement:
    """MERGEs on the natural key (port_id, cargo_type_id, year, direction,
    source). OUTPUT captures both the old and new weight on a match, so
    the loader can tell a genuine re-run (identical value) apart from a
    revised figure (Eurostat changed the number) and flag the latter as
    revised_estimate -- see load/loader.py."""
    sql = """
        MERGE dbo.port_throughput AS target
        USING (
            SELECT ? AS port_id, ? AS cargo_type_id, ? AS year, ? AS direction, ? AS source
        ) AS src
        ON target.port_id = src.port_id
            AND target.cargo_type_id = src.cargo_type_id
            AND target.year = src.year
            AND target.direction = src.direction
            AND target.source = src.source
        WHEN MATCHED THEN
            UPDATE SET gross_weight_tonnes = ?
        WHEN NOT MATCHED THEN
            INSERT (port_id, cargo_type_id, year, direction, gross_weight_tonnes, source)
            VALUES (?, ?, ?, ?, ?, ?)
        OUTPUT
            $action,
            inserted.throughput_id,
            deleted.gross_weight_tonnes,
            inserted.gross_weight_tonnes;
    """
    params = (
        port_id,
        cargo_type_id,
        row.year,
        row.direction.value,
        row.source,
        row.gross_weight_tonnes,
        port_id,
        cargo_type_id,
        row.year,
        row.direction.value,
        row.gross_weight_tonnes,
        row.source,
    )
    return Statement(sql=sql, params=params)


def build_flag_upsert(
    flag: DataQualityFlag,
    port_id: int | None,
    throughput_id: int | None,
) -> Statement:
    """Insert-only: a flag is a fact about the data, not something that
    changes value on re-run. MERGEs on (throughput_id, port_id, flag_type,
    description) with explicit NULL-safe equality, since T-SQL's `=`
    never matches NULL to NULL."""
    sql = """
        MERGE dbo.data_quality_flags AS target
        USING (
            SELECT ? AS throughput_id, ? AS port_id, ? AS flag_type, ? AS description
        ) AS src
        ON (target.throughput_id = src.throughput_id
                OR (target.throughput_id IS NULL AND src.throughput_id IS NULL))
            AND (target.port_id = src.port_id
                OR (target.port_id IS NULL AND src.port_id IS NULL))
            AND target.flag_type = src.flag_type
            AND target.description = src.description
        WHEN NOT MATCHED THEN
            INSERT (throughput_id, port_id, flag_type, description, resolution)
            VALUES (?, ?, ?, ?, ?);
    """
    params = (
        throughput_id,
        port_id,
        flag.flag_type.value,
        flag.description,
        throughput_id,
        port_id,
        flag.flag_type.value,
        flag.description,
        flag.resolution,
    )
    return Statement(sql=sql, params=params)
