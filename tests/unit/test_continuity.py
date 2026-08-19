from __future__ import annotations

from port_analytics.models import Direction, FlagType, PortThroughputRow
from port_analytics.transform.continuity import (
    DERIVED_SOURCE,
    MERGED_PORT_CODE,
    derive_pre_merger_antwerp_bruges,
)


def _row(
    port_code: str, cargo: str, year: int, direction: Direction, value: float
) -> PortThroughputRow:
    return PortThroughputRow(
        port_code=port_code,
        cargo_type_code=cargo,
        year=year,
        direction=direction,
        gross_weight_tonnes=value,
        source="mar_mg_aa_pwhd" if cargo == "TOTAL" else "mar_mg_am_pwhc",
    )


def test_sums_legacy_ports_for_years_before_the_merger() -> None:
    rows = [
        _row("BE_0BEANR", "TOTAL", 2021, Direction.TOTAL, 215852),
        _row("BE_0BEZEE", "TOTAL", 2021, Direction.TOTAL, 40130),
    ]

    derived, _flags = derive_pre_merger_antwerp_bruges(rows)

    assert len(derived) == 1
    assert derived[0].port_code == MERGED_PORT_CODE
    assert derived[0].year == 2021
    assert derived[0].gross_weight_tonnes == 215852 + 40130
    assert derived[0].source == DERIVED_SOURCE


def test_ignores_legacy_rows_from_the_merger_year_onward() -> None:
    rows = [
        _row("BE_0BEANR", "TOTAL", 2021, Direction.TOTAL, 100),
        _row("BE_0BEZEE", "TOTAL", 2021, Direction.TOTAL, 50),
        # Legacy ports don't actually report from 2022 on, but even if a
        # stray row showed up here, it must not be summed — 2022+ belongs
        # to the merged entity's own reporting.
        _row("BE_0BEANR", "TOTAL", 2022, Direction.TOTAL, 999),
    ]

    derived, _flags = derive_pre_merger_antwerp_bruges(rows)

    assert [d.year for d in derived] == [2021]


def test_never_derives_from_the_merged_ports_own_rows() -> None:
    rows = [_row(MERGED_PORT_CODE, "TOTAL", 2022, Direction.TOTAL, 254257)]

    derived, _flags = derive_pre_merger_antwerp_bruges(rows)

    assert derived == []


def test_does_not_mutate_or_drop_the_input_rows() -> None:
    rows = [
        _row("BE_0BEANR", "TOTAL", 2021, Direction.TOTAL, 215852),
        _row("BE_0BEZEE", "TOTAL", 2021, Direction.TOTAL, 40130),
    ]
    original = list(rows)

    derive_pre_merger_antwerp_bruges(rows)

    assert rows == original


def test_skips_deriving_when_only_one_legacy_port_has_a_value() -> None:
    rows = [_row("BE_0BEANR", "TOTAL", 2021, Direction.TOTAL, 215852)]

    derived, flags = derive_pre_merger_antwerp_bruges(rows)

    assert derived == []
    incomplete = [f for f in flags if f.flag_type == FlagType.MISSING_YEAR]
    assert len(incomplete) == 1
    assert incomplete[0].port_code == MERGED_PORT_CODE
    assert "BE_0BEZEE" in incomplete[0].description


def test_always_flags_both_legacy_ports_as_merged() -> None:
    _derived, flags = derive_pre_merger_antwerp_bruges([])

    merger_flags = [f for f in flags if f.flag_type == FlagType.PORT_MERGER]
    flagged_ports = {f.port_code for f in merger_flags}
    assert "BE_0BEANR" in flagged_ports
    assert "BE_0BEZEE" in flagged_ports


def test_flags_the_derived_series_as_imperfect_when_rows_exist() -> None:
    rows = [
        _row("BE_0BEANR", "TOTAL", 2021, Direction.TOTAL, 215852),
        _row("BE_0BEZEE", "TOTAL", 2021, Direction.TOTAL, 40130),
    ]

    _derived, flags = derive_pre_merger_antwerp_bruges(rows)

    summary = next(
        f for f in flags if f.flag_type == FlagType.PORT_MERGER and f.port_code == MERGED_PORT_CODE
    )
    # This is the explicit, documented decision the project hinges on —
    # the flag must actually say it's a derived figure and name a real
    # imperfection, not just assert the merger happened.
    assert "not" in summary.description.lower()
    assert "eurostat" in summary.description.lower()
    assert DERIVED_SOURCE in summary.description


def test_no_derived_series_flag_when_nothing_was_derived() -> None:
    _derived, flags = derive_pre_merger_antwerp_bruges([])

    assert not any(f.port_code == MERGED_PORT_CODE for f in flags)


def test_sums_independently_per_cargo_type_and_direction() -> None:
    rows = [
        _row("BE_0BEANR", "TOTAL", 2020, Direction.TOTAL, 200),
        _row("BE_0BEZEE", "TOTAL", 2020, Direction.TOTAL, 30),
        _row("BE_0BEANR", "LBK", 2020, Direction.TOTAL, 70),
        _row("BE_0BEZEE", "LBK", 2020, Direction.TOTAL, 9),
        _row("BE_0BEANR", "TOTAL", 2020, Direction.INBOUND, 120),
        _row("BE_0BEZEE", "TOTAL", 2020, Direction.INBOUND, 15),
    ]

    derived, _flags = derive_pre_merger_antwerp_bruges(rows)

    by_key = {(d.cargo_type_code, d.direction): d.gross_weight_tonnes for d in derived}
    assert by_key[("TOTAL", Direction.TOTAL)] == 230
    assert by_key[("LBK", Direction.TOTAL)] == 79
    assert by_key[("TOTAL", Direction.INBOUND)] == 135
