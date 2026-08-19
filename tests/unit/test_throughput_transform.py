from __future__ import annotations

from typing import Any

from port_analytics.models import Direction, FlagType
from port_analytics.transform.throughput import (
    _all_years,
    _eligible_years,
    _gap_flags,
    build_cargo_rows,
    build_direction_rows,
)


def _jsonstat_payload(
    dim_ids: list[str],
    categories: dict[str, list[str]],
    entries: dict[tuple[str, ...], float],
) -> dict[str, Any]:
    """Builds a valid minimal JSON-stat 2.0 payload from a dict of
    dimension-code tuples (in `dim_ids` order) to values, computing the
    flat row-major index the same way the real API does."""
    sizes = [len(categories[d]) for d in dim_ids]
    strides = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]
    positions = {d: {c: i for i, c in enumerate(categories[d])} for d in dim_ids}

    value: dict[str, float] = {}
    for codes, val in entries.items():
        flat = sum(positions[d][c] * s for d, c, s in zip(dim_ids, codes, strides, strict=True))
        value[str(flat)] = val

    return {
        "version": "2.0",
        "class": "dataset",
        "label": "fixture",
        "source": "ESTAT",
        "updated": "2025-12-03T23:00:00+0100",
        "id": dim_ids,
        "size": sizes,
        "dimension": {
            d: {"category": {"index": {c: i for i, c in enumerate(categories[d])}}} for d in dim_ids
        },
        "value": value,
        "extension": {},
    }


DIRECTION_DIMS = ["freq", "unit", "direct", "rep_mar", "time"]
DIRECTION_CATEGORIES = {
    "freq": ["A"],
    "unit": ["THS_T", "RT_PRE"],
    "direct": ["TOTAL", "IN", "OUT"],
    "rep_mar": ["BE_0BEANR", "DE_1DEHAM"],
    "time": ["2020", "2021"],
}


def test_build_direction_rows_maps_codes_and_tags_source() -> None:
    payload = _jsonstat_payload(
        DIRECTION_DIMS,
        DIRECTION_CATEGORIES,
        {
            ("A", "THS_T", "TOTAL", "BE_0BEANR", "2020"): 206319,
            ("A", "THS_T", "TOTAL", "BE_0BEANR", "2021"): 215852,
            ("A", "THS_T", "IN", "BE_0BEANR", "2020"): 100000,
            ("A", "THS_T", "IN", "BE_0BEANR", "2021"): 105000,
            ("A", "THS_T", "OUT", "BE_0BEANR", "2020"): 106319,
            ("A", "THS_T", "OUT", "BE_0BEANR", "2021"): 110852,
            ("A", "THS_T", "TOTAL", "DE_1DEHAM", "2020"): 109175,
            ("A", "THS_T", "TOTAL", "DE_1DEHAM", "2021"): 111156,
            ("A", "THS_T", "IN", "DE_1DEHAM", "2020"): 50000,
            ("A", "THS_T", "IN", "DE_1DEHAM", "2021"): 51000,
            ("A", "THS_T", "OUT", "DE_1DEHAM", "2020"): 59175,
            ("A", "THS_T", "OUT", "DE_1DEHAM", "2021"): 60156,
        },
    )

    rows, flags = build_direction_rows(payload)

    assert len(rows) == 12
    assert all(r.cargo_type_code == "TOTAL" for r in rows)
    assert all(r.source == "mar_mg_aa_pwhd" for r in rows)
    antwerpen_total_2021 = next(
        r
        for r in rows
        if r.port_code == "BE_0BEANR" and r.year == 2021 and r.direction == Direction.TOTAL
    )
    assert antwerpen_total_2021.gross_weight_tonnes == 215852
    # BE_0BEANR and DE_1DEHAM are fully populated for both years and all
    # three directions in this fixture, so neither should be flagged.
    # (Other registered ports absent from this minimal fixture entirely
    # are expected to generate their own flags — not under test here.)
    relevant_flags = [f for f in flags if f.port_code in {"BE_0BEANR", "DE_1DEHAM"}]
    assert relevant_flags == []


def test_build_direction_rows_filters_non_ths_t_unit() -> None:
    payload = _jsonstat_payload(
        DIRECTION_DIMS,
        DIRECTION_CATEGORIES,
        {
            ("A", "THS_T", "TOTAL", "BE_0BEANR", "2020"): 206319,
            ("A", "RT_PRE", "TOTAL", "BE_0BEANR", "2020"): 2.4,
        },
    )

    rows, _flags = build_direction_rows(payload)

    assert len(rows) == 1
    assert rows[0].gross_weight_tonnes == 206319


def test_build_direction_rows_ignores_unrecognised_port_codes() -> None:
    categories = {**DIRECTION_CATEGORIES, "rep_mar": ["BE_0BEANR", "FR_UNKNOWN"]}
    payload = _jsonstat_payload(
        DIRECTION_DIMS,
        categories,
        {
            ("A", "THS_T", "TOTAL", "BE_0BEANR", "2020"): 206319,
            ("A", "THS_T", "TOTAL", "FR_UNKNOWN", "2020"): 999,
        },
    )

    rows, _flags = build_direction_rows(payload)

    assert len(rows) == 1
    assert rows[0].port_code == "BE_0BEANR"


CARGO_DIMS = ["freq", "unit", "cargo", "rep_mar", "time"]
CARGO_CATEGORIES = {
    "freq": ["A"],
    "unit": ["THS_T", "PC_TOT"],
    "cargo": ["TOTAL", "LBK", "DBK", "LCNT", "RO_MSP", "RO_MNSP", "OTH", "UNK"],
    "rep_mar": ["BE_0BE003"],
    "time": ["2022"],
}


def test_build_cargo_rows_excludes_total_and_unk() -> None:
    payload = _jsonstat_payload(
        CARGO_DIMS,
        CARGO_CATEGORIES,
        {
            ("A", "THS_T", "TOTAL", "BE_0BE003", "2022"): 254257,
            ("A", "THS_T", "LBK", "BE_0BE003", "2022"): 89729,
            ("A", "THS_T", "UNK", "BE_0BE003", "2022"): 1,
        },
    )

    rows, _flags = build_cargo_rows(payload)

    assert len(rows) == 1
    assert rows[0].cargo_type_code == "LBK"
    assert rows[0].direction == Direction.TOTAL
    assert rows[0].source == "mar_mg_am_pwhc"


def test_build_cargo_rows_filters_non_ths_t_unit() -> None:
    payload = _jsonstat_payload(
        CARGO_DIMS,
        CARGO_CATEGORIES,
        {
            ("A", "THS_T", "LBK", "BE_0BE003", "2022"): 89729,
            ("A", "PC_TOT", "LBK", "BE_0BE003", "2022"): 35.3,
        },
    )

    rows, _flags = build_cargo_rows(payload)

    assert len(rows) == 1
    assert rows[0].gross_weight_tonnes == 89729


def test_all_years_reads_the_time_dimension() -> None:
    payload = _jsonstat_payload(DIRECTION_DIMS, DIRECTION_CATEGORIES, {})
    assert _all_years(payload) == [2020, 2021]


def test_eligible_years_restricts_legacy_ports_to_pre_merger() -> None:
    years = list(range(2019, 2025))
    assert _eligible_years("BE_0BEANR", years) == [2019, 2020, 2021]


def test_eligible_years_restricts_merged_port_to_post_merger() -> None:
    years = list(range(2019, 2025))
    assert _eligible_years("BE_0BE003", years) == [2022, 2023, 2024]


def test_eligible_years_covers_full_range_for_unrelated_ports() -> None:
    years = list(range(2019, 2025))
    assert _eligible_years("DE_1DEHAM", years) == years


def test_gap_flags_returns_nothing_when_fully_present() -> None:
    flags = _gap_flags("DE_1DEHAM", "TOTAL", Direction.TOTAL, [2020, 2021], {2020, 2021}, "src")
    assert flags == []


def test_gap_flags_classifies_trailing_gap_as_code_change() -> None:
    # Mirrors the real finding: Hamburg reported RO_MNSP 2005-2011, then
    # nothing 2012-2024.
    years = list(range(2005, 2013))
    present = set(range(2005, 2012))  # missing only 2012

    flags = _gap_flags("DE_1DEHAM", "RO_MNSP", Direction.TOTAL, years, present, "mar_mg_am_pwhc")

    assert len(flags) == 1
    assert flags[0].flag_type == FlagType.CODE_CHANGE
    assert flags[0].port_code == "DE_1DEHAM"
    assert "2012" in flags[0].description
    assert "2011" in flags[0].description


def test_gap_flags_classifies_leading_gap_as_code_change() -> None:
    years = [2019, 2020, 2021]
    present = {2020, 2021}  # missing the first year only

    flags = _gap_flags("DE_1DEHAM", "TOTAL", Direction.TOTAL, years, present, "src")

    assert len(flags) == 1
    assert flags[0].flag_type == FlagType.CODE_CHANGE


def test_gap_flags_classifies_scattered_gap_as_missing_year_per_year() -> None:
    years = [2019, 2020, 2021, 2022]
    present = {2019, 2021}  # 2020 and 2022 missing, not contiguous at an edge

    flags = _gap_flags("DE_1DEHAM", "TOTAL", Direction.TOTAL, years, present, "src")

    assert len(flags) == 2
    assert all(f.flag_type == FlagType.MISSING_YEAR for f in flags)
    assert {f.throughput_ref.year for f in flags if f.throughput_ref} == {2020, 2022}
