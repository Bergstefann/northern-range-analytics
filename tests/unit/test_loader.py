"""Tests the actual conditional logic in loader.py (revision detection,
throughput_id resolution for flags) against a fake pyodbc-shaped cursor.
The MERGE statements themselves are unit-tested in test_load_upsert.py;
this file doesn't re-test SQL text, just the orchestration logic around
each statement's result."""

from __future__ import annotations

from typing import Any

from port_analytics.load.loader import upsert_flags, upsert_throughput_rows
from port_analytics.models import (
    DataQualityFlag,
    Direction,
    FlagType,
    PortThroughputRow,
    ThroughputRef,
)


class FakeCursor:
    def __init__(self, fetchone_results: list[Any]) -> None:
        self._results = iter(fetchone_results)
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.executed.append((sql, params))

    def fetchone(self) -> Any:
        return next(self._results)


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.commits = 0

    def cursor(self) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1


def _row(port_code: str, cargo: str, year: int, value: float) -> PortThroughputRow:
    return PortThroughputRow(
        port_code=port_code,
        cargo_type_code=cargo,
        year=year,
        direction=Direction.TOTAL,
        gross_weight_tonnes=value,
        source="mar_mg_aa_pwhd",
    )


def test_new_row_is_not_flagged_as_a_revision() -> None:
    cursor = FakeCursor([("INSERT", 501, None, 111156000.0)])
    conn = FakeConnection(cursor)
    rows = [_row("DE_1DEHAM", "TOTAL", 2021, 111156000.0)]

    throughput_ids, revision_flags = upsert_throughput_rows(
        conn, rows, {"DE_1DEHAM": 4}, {"TOTAL": 1}
    )

    assert throughput_ids[("DE_1DEHAM", "TOTAL", 2021, "total")] == 501
    assert revision_flags == []


def test_unchanged_value_on_reload_is_not_flagged_as_a_revision() -> None:
    cursor = FakeCursor([("UPDATE", 501, 111156000.0, 111156000.0)])
    conn = FakeConnection(cursor)
    rows = [_row("DE_1DEHAM", "TOTAL", 2021, 111156000.0)]

    _ids, revision_flags = upsert_throughput_rows(conn, rows, {"DE_1DEHAM": 4}, {"TOTAL": 1})

    assert revision_flags == []


def test_changed_value_on_reload_is_flagged_as_a_revision() -> None:
    cursor = FakeCursor([("UPDATE", 501, 111156000.0, 120000000.0)])
    conn = FakeConnection(cursor)
    rows = [_row("DE_1DEHAM", "TOTAL", 2021, 120000000.0)]

    _ids, revision_flags = upsert_throughput_rows(conn, rows, {"DE_1DEHAM": 4}, {"TOTAL": 1})

    assert len(revision_flags) == 1
    flag = revision_flags[0]
    assert flag.flag_type == FlagType.REVISED_ESTIMATE
    assert flag.port_code == "DE_1DEHAM"
    assert "111156000.0" in flag.description
    assert "120000000.0" in flag.description


def test_upsert_flags_resolves_a_matching_throughput_ref() -> None:
    cursor = FakeCursor([None])
    conn = FakeConnection(cursor)
    flag = DataQualityFlag(
        flag_type=FlagType.OUTLIER_SUSPECTED,
        description="Suspiciously large jump.",
        resolution="Documented, not corrected.",
        throughput_ref=ThroughputRef(
            port_code="DE_1DEHAM", cargo_type_code="TOTAL", year=2021, direction=Direction.TOTAL
        ),
    )
    throughput_ids = {("DE_1DEHAM", "TOTAL", 2021, "total"): 501}

    upsert_flags(conn, [flag], port_ids={}, throughput_ids=throughput_ids)

    _sql, params = cursor.executed[0]
    assert params[0] == 501  # throughput_id in the USING clause


def test_upsert_flags_leaves_throughput_id_none_when_the_ref_does_not_resolve() -> None:
    cursor = FakeCursor([None])
    conn = FakeConnection(cursor)
    flag = DataQualityFlag(
        flag_type=FlagType.MISSING_YEAR,
        description="No data reported.",
        resolution="Left absent.",
        throughput_ref=ThroughputRef(
            port_code="DE_1DEHAM", cargo_type_code="RO_MNSP", year=2015, direction=Direction.TOTAL
        ),
    )

    upsert_flags(conn, [flag], port_ids={}, throughput_ids={})

    _sql, params = cursor.executed[0]
    assert params[0] is None


def test_upsert_flags_resolves_port_id_from_port_code() -> None:
    cursor = FakeCursor([None])
    conn = FakeConnection(cursor)
    flag = DataQualityFlag(
        flag_type=FlagType.PORT_MERGER,
        port_code="BE_0BEANR",
        description="Antwerpen merged into Antwerp-Bruges from 2022.",
        resolution="Linked via ports.merged_into_port_id.",
    )

    upsert_flags(conn, [flag], port_ids={"BE_0BEANR": 2}, throughput_ids={})

    _sql, params = cursor.executed[0]
    assert params[1] == 2  # port_id in the USING clause
