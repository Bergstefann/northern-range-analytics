"""End-to-end wiring test: both raw datasets -> transform_all() -> rows +
flags, including the Antwerp continuity derivation on top. Self-contained
fixtures, no network and no dependency on locally-landed raw files."""

from __future__ import annotations

from typing import Any

from port_analytics.models import Direction, FlagType
from port_analytics.transform.continuity import MERGED_PORT_CODE
from port_analytics.transform.pipeline import transform_all


def _jsonstat_payload(
    dim_ids: list[str],
    categories: dict[str, list[str]],
    entries: dict[tuple[str, ...], float],
) -> dict[str, Any]:
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
    "unit": ["THS_T"],
    "direct": ["TOTAL", "IN", "OUT"],
    "rep_mar": ["BE_0BE003", "BE_0BEANR", "BE_0BEZEE", "DE_1DEHAM", "NL_0NLRTM", "PL_0PLGDN"],
    "time": ["2021", "2022"],
}

CARGO_DIMS = ["freq", "unit", "cargo", "rep_mar", "time"]
CARGO_CATEGORIES = {
    "freq": ["A"],
    "unit": ["THS_T"],
    "cargo": ["TOTAL", "LBK", "DBK", "LCNT", "RO_MSP", "RO_MNSP", "OTH", "UNK"],
    "rep_mar": ["BE_0BE003", "BE_0BEANR", "BE_0BEZEE", "DE_1DEHAM", "NL_0NLRTM", "PL_0PLGDN"],
    "time": ["2021", "2022"],
}


def _full_direction_entries() -> dict[tuple[str, ...], float]:
    entries: dict[tuple[str, ...], float] = {}
    for port in DIRECTION_CATEGORIES["rep_mar"]:
        for direct in DIRECTION_CATEGORIES["direct"]:
            for year in DIRECTION_CATEGORIES["time"]:
                # Legacy ports don't report 2022; the merged port doesn't
                # report 2021 -- mirrors the real merger cutover exactly.
                if port in ("BE_0BEANR", "BE_0BEZEE") and year == "2022":
                    continue
                if port == "BE_0BE003" and year == "2021":
                    continue
                entries[("A", "THS_T", direct, port, year)] = 1000.0
    return entries


def _full_cargo_entries() -> dict[tuple[str, ...], float]:
    entries: dict[tuple[str, ...], float] = {}
    for port in CARGO_CATEGORIES["rep_mar"]:
        for cargo in ("LBK", "DBK", "LCNT", "RO_MSP", "RO_MNSP", "OTH"):
            for year in CARGO_CATEGORIES["time"]:
                if port in ("BE_0BEANR", "BE_0BEZEE") and year == "2022":
                    continue
                if port == "BE_0BE003" and year == "2021":
                    continue
                entries[("A", "THS_T", cargo, port, year)] = 100.0
    return entries


def test_transform_all_wires_both_datasets_and_the_continuity_derivation() -> None:
    direction_payload = _jsonstat_payload(
        DIRECTION_DIMS, DIRECTION_CATEGORIES, _full_direction_entries()
    )
    cargo_payload = _jsonstat_payload(CARGO_DIMS, CARGO_CATEGORIES, _full_cargo_entries())

    rows, flags = transform_all(direction_payload, cargo_payload)

    assert len(rows) > 0

    # Direct rows from both datasets are present, correctly source-tagged.
    assert any(r.source == "mar_mg_aa_pwhd" for r in rows)
    assert any(r.source == "mar_mg_am_pwhc" for r in rows)

    # The continuity derivation ran on top: a 2021 Antwerp-Bruges row
    # exists even though BE_0BE003 itself never reports 2021.
    derived = [r for r in rows if r.source.startswith("derived_sum:")]
    assert any(
        r.port_code == MERGED_PORT_CODE and r.year == 2021 and r.direction == Direction.TOTAL
        for r in derived
    )
    # Legacy rows are still there too -- nothing was dropped in favour of
    # the derived view.
    assert any(r.port_code == "BE_0BEANR" and r.year == 2021 for r in rows)
    assert any(r.port_code == "BE_0BEZEE" and r.year == 2021 for r in rows)

    port_merger_flags = [f for f in flags if f.flag_type == FlagType.PORT_MERGER]
    assert {f.port_code for f in port_merger_flags} == {"BE_0BEANR", "BE_0BEZEE", MERGED_PORT_CODE}

    # This fixture is fully populated except for the merger cutover, so
    # nothing should be flagged missing_year or code_change.
    assert not [f for f in flags if f.flag_type in (FlagType.MISSING_YEAR, FlagType.CODE_CHANGE)]
