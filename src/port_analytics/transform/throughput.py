"""Builds port_throughput rows from each raw Eurostat dataset and flags
gaps in the (port, cargo_type, direction) coverage that aren't explained
by the Antwerp-Bruges merger (that's handled separately — see
transform/continuity.py).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from port_analytics.models import (
    DataQualityFlag,
    Direction,
    FlagType,
    PortThroughputRow,
    ThroughputRef,
)
from port_analytics.transform.jsonstat import decode_observations
from port_analytics.transform.reference_data import (
    ANTWERP_BRUGES_MERGER_YEAR,
    CARGO_TYPES,
    DIRECTION_CODES,
    PORTS,
)

DIRECTION_DATASET_CODE = "mar_mg_aa_pwhd"
CARGO_DATASET_CODE = "mar_mg_am_pwhc"


def build_direction_rows(
    payload: dict[str, Any],
) -> tuple[list[PortThroughputRow], list[DataQualityFlag]]:
    """mar_mg_aa_pwhd: gross weight by port and direction, no cargo
    breakdown — every row is cargo_type_code='TOTAL'."""
    all_years = _all_years(payload)
    rows: list[PortThroughputRow] = []
    presence: dict[tuple[str, str], set[int]] = defaultdict(set)

    for obs in decode_observations(payload):
        dv = obs.dimension_values
        if dv.get("unit") != "THS_T":
            continue
        port_code = dv.get("rep_mar")
        direct_code = dv.get("direct")
        if port_code not in PORTS or direct_code not in DIRECTION_CODES:
            continue
        year = int(dv["time"])
        rows.append(
            PortThroughputRow(
                port_code=port_code,
                cargo_type_code="TOTAL",
                year=year,
                direction=DIRECTION_CODES[direct_code],
                gross_weight_tonnes=obs.value,
                source=DIRECTION_DATASET_CODE,
            )
        )
        presence[(port_code, direct_code)].add(year)

    flags: list[DataQualityFlag] = []
    for port_code in PORTS:
        eligible = _eligible_years(port_code, all_years)
        for direct_code, direction in DIRECTION_CODES.items():
            flags.extend(
                _gap_flags(
                    port_code,
                    "TOTAL",
                    direction,
                    eligible,
                    presence[(port_code, direct_code)],
                    DIRECTION_DATASET_CODE,
                )
            )

    return rows, flags


def build_cargo_rows(
    payload: dict[str, Any],
) -> tuple[list[PortThroughputRow], list[DataQualityFlag]]:
    """mar_mg_am_pwhc: gross weight by port and cargo type, no direction
    breakdown — every row is direction=TOTAL.

    cargo='TOTAL' is deliberately excluded: mar_mg_aa_pwhd already
    supplies the cargo_type_code='TOTAL' row, so loading it here too
    would create two independently-sourced "total" rows for the same
    port/year. cargo='UNK' is excluded because it has zero data points
    everywhere (docs/data-quality-notes.md, Finding 2) — this check is
    just defensive.
    """
    all_years = _all_years(payload)
    rows: list[PortThroughputRow] = []
    presence: dict[tuple[str, str], set[int]] = defaultdict(set)
    cargo_codes = [code for code in CARGO_TYPES if code != "TOTAL"]

    for obs in decode_observations(payload):
        dv = obs.dimension_values
        if dv.get("unit") != "THS_T":
            continue
        port_code = dv.get("rep_mar")
        cargo_code = dv.get("cargo")
        if port_code not in PORTS or cargo_code not in cargo_codes:
            continue
        year = int(dv["time"])
        rows.append(
            PortThroughputRow(
                port_code=port_code,
                cargo_type_code=cargo_code,
                year=year,
                direction=Direction.TOTAL,
                gross_weight_tonnes=obs.value,
                source=CARGO_DATASET_CODE,
            )
        )
        presence[(port_code, cargo_code)].add(year)

    flags: list[DataQualityFlag] = []
    for port_code in PORTS:
        eligible = _eligible_years(port_code, all_years)
        for cargo_code in cargo_codes:
            flags.extend(
                _gap_flags(
                    port_code,
                    cargo_code,
                    Direction.TOTAL,
                    eligible,
                    presence[(port_code, cargo_code)],
                    CARGO_DATASET_CODE,
                )
            )

    return rows, flags


def _all_years(payload: dict[str, Any]) -> list[int]:
    time_index: dict[str, int] = payload["dimension"]["time"]["category"]["index"]
    return sorted(int(year) for year in time_index)


def _eligible_years(port_code: str, all_years: list[int]) -> list[int]:
    """Years this port could plausibly report in, given the Antwerp-Bruges
    merger cutover — a legacy port isn't expected to report after it
    merged, and the merged entity isn't expected to report before it
    existed. Every other port is expected across the full range."""
    port = PORTS[port_code]
    if port.merged_into is not None:
        return [y for y in all_years if y < ANTWERP_BRUGES_MERGER_YEAR]
    if any(p.merged_into == port_code for p in PORTS.values()):
        return [y for y in all_years if y >= ANTWERP_BRUGES_MERGER_YEAR]
    return list(all_years)


def _gap_flags(
    port_code: str,
    cargo_type_code: str,
    direction: Direction,
    eligible_years: list[int],
    present_years: set[int],
    source: str,
) -> list[DataQualityFlag]:
    """Classifies gaps in one (port, cargo_type, direction) series.

    A gap that's a clean contiguous block at the start or end of the
    eligible range (reporting stopped, or started late) is flagged once
    as code_change — that pattern suggests a reporting/methodology
    change, not random non-reporting. Anything else (scattered single
    years) gets one missing_year flag per missing year.
    """
    sorted_years = sorted(eligible_years)
    missing = [y for y in sorted_years if y not in present_years]
    if not missing:
        return []

    port_name = PORTS[port_code].port_name
    cargo_name = CARGO_TYPES[cargo_type_code].cargo_type_name
    tail_len = len(missing)

    if tail_len < len(sorted_years) and sorted_years[-tail_len:] == missing:
        last_present = sorted_years[-tail_len - 1]
        return [
            DataQualityFlag(
                flag_type=FlagType.CODE_CHANGE,
                port_code=port_code,
                description=(
                    f"{port_name} reported {cargo_name} ({direction.value}) in {source} "
                    f"through {last_present} but has zero rows for {missing[0]}-{missing[-1]}. "
                    "Clean one-directional cutoff (present, then never again) rather than "
                    "scattered gaps, suggesting a reporting or classification change — not "
                    "confirmed from the API alone."
                ),
                resolution=(
                    f"Left absent in port_throughput for {missing[0]}-{missing[-1]} (no "
                    "fabricated zero inserted); flagged as code_change rather than "
                    "missing_year because of the clean cutoff pattern."
                ),
            )
        ]

    if tail_len < len(sorted_years) and sorted_years[:tail_len] == missing:
        first_present = sorted_years[tail_len]
        return [
            DataQualityFlag(
                flag_type=FlagType.CODE_CHANGE,
                port_code=port_code,
                description=(
                    f"{port_name} has zero rows for {cargo_name} ({direction.value}) in "
                    f"{source} before {first_present}, then reports every year from "
                    f"{first_present} onward. Clean one-directional start, suggesting "
                    "reporting began or a category was introduced at that point — not "
                    "confirmed from the API alone."
                ),
                resolution=(
                    f"Left absent in port_throughput for {missing[0]}-{missing[-1]} (no "
                    "fabricated zero inserted); flagged as code_change rather than "
                    "missing_year because of the clean cutoff pattern."
                ),
            )
        ]

    return [
        DataQualityFlag(
            flag_type=FlagType.MISSING_YEAR,
            port_code=port_code,
            throughput_ref=ThroughputRef(
                port_code=port_code,
                cargo_type_code=cargo_type_code,
                year=year,
                direction=direction,
            ),
            description=(
                f"No {source} data reported for {port_name}, {cargo_name}, "
                f"{direction.value}, {year}."
            ),
            resolution="Left absent in port_throughput; no fabricated zero was inserted.",
        )
        for year in missing
    ]
