"""The Antwerp continuity decision.

Eurostat never published a unified pre-2022 "Antwerp-Bruges" figure —
Antwerpen (BE_0BEANR) and Zeebrugge (BE_0BEZEE) reported separately
through 2021, and the merged entity (BE_0BE003) reports from 2022. This
module makes the resulting design decision explicit rather than leaving
it implicit in transform logic:

  - The raw per-legacy-port rows are always kept, untouched, under their
    own port codes (source=mar_mg_aa_pwhd / mar_mg_am_pwhc).
  - IN ADDITION, this module derives a continuous pre-2022 Antwerp-Bruges
    series by summing Antwerpen + Zeebrugge for every (cargo_type,
    direction, year) where both legacy ports reported a value, and
    stores it under BE_0BE003 with source='derived_sum:...' so it's
    never mistaken for an Eurostat-reported figure.
  - Every derived row is backed by a port_merger data_quality_flags row
    (see _merger_summary_flags) that states plainly this is a computed
    figure and names the ways it could be wrong — see docs/data-quality-notes.md,
    'Antwerp continuity decision', for the full reasoning.

Both views end up in port_throughput. A downstream consumer who wants
strict source fidelity queries the legacy port codes via
ports.merged_into_port_id; a consumer who wants one continuous Antwerp
series queries port_id=BE_0BE003 and gets it, clearly source-tagged.
"""

from __future__ import annotations

from port_analytics.models import (
    DataQualityFlag,
    Direction,
    FlagType,
    PortThroughputRow,
    ThroughputRef,
)
from port_analytics.transform.reference_data import ANTWERP_BRUGES_MERGER_YEAR

MERGED_PORT_CODE = "BE_0BE003"
LEGACY_PORT_CODES = ("BE_0BEANR", "BE_0BEZEE")
DERIVED_SOURCE = "derived_sum:BE_0BEANR+BE_0BEZEE"


def derive_pre_merger_antwerp_bruges(
    rows: list[PortThroughputRow],
) -> tuple[list[PortThroughputRow], list[DataQualityFlag]]:
    """Returns new derived rows (never mutates or removes anything in
    `rows`) plus the data_quality_flags rows documenting the decision."""
    values_by_key: dict[tuple[str, str, int], dict[str, float]] = {}
    for row in rows:
        if row.port_code not in LEGACY_PORT_CODES or row.year >= ANTWERP_BRUGES_MERGER_YEAR:
            continue
        key = (row.cargo_type_code, row.direction.value, row.year)
        values_by_key.setdefault(key, {})[row.port_code] = row.gross_weight_tonnes

    derived_rows: list[PortThroughputRow] = []
    incomplete_flags: list[DataQualityFlag] = []

    for (cargo_type_code, direction_value, year), values in sorted(
        values_by_key.items(), key=lambda kv: kv[0]
    ):
        if len(values) < len(LEGACY_PORT_CODES):
            missing_port = next(p for p in LEGACY_PORT_CODES if p not in values)
            incomplete_flags.append(
                DataQualityFlag(
                    flag_type=FlagType.MISSING_YEAR,
                    port_code=MERGED_PORT_CODE,
                    throughput_ref=ThroughputRef(
                        port_code=MERGED_PORT_CODE,
                        cargo_type_code=cargo_type_code,
                        year=year,
                        direction=Direction(direction_value),
                    ),
                    description=(
                        f"Cannot derive a pre-2022 Antwerp-Bruges total for {cargo_type_code} "
                        f"({direction_value}) {year}: {missing_port} has no reported value "
                        "for that combination, so summing would silently understate the total."
                    ),
                    resolution="No derived row created for this combination; left absent.",
                )
            )
            continue

        derived_rows.append(
            PortThroughputRow(
                port_code=MERGED_PORT_CODE,
                cargo_type_code=cargo_type_code,
                year=year,
                direction=Direction(direction_value),
                gross_weight_tonnes=sum(values.values()),
                source=DERIVED_SOURCE,
            )
        )

    flags = _merger_summary_flags(has_derived_rows=bool(derived_rows)) + incomplete_flags
    return derived_rows, flags


def _merger_summary_flags(*, has_derived_rows: bool) -> list[DataQualityFlag]:
    flags = [
        DataQualityFlag(
            flag_type=FlagType.PORT_MERGER,
            port_code="BE_0BEANR",
            description=(
                "Antwerpen merged into Antwerp-Bruges from 2022. Eurostat reports it "
                "separately (as BE_0BEANR) only through 2021."
            ),
            resolution="Linked via ports.merged_into_port_id; retained as-reported, not altered.",
        ),
        DataQualityFlag(
            flag_type=FlagType.PORT_MERGER,
            port_code="BE_0BEZEE",
            description=(
                "Zeebrugge merged into Antwerp-Bruges from 2022. Eurostat reports it "
                "separately (as BE_0BEZEE) only through 2021."
            ),
            resolution="Linked via ports.merged_into_port_id; retained as-reported, not altered.",
        ),
    ]

    if has_derived_rows:
        flags.append(
            DataQualityFlag(
                flag_type=FlagType.PORT_MERGER,
                port_code=MERGED_PORT_CODE,
                description=(
                    "Pre-2022 Antwerp-Bruges port_throughput rows "
                    f"(source='{DERIVED_SOURCE}') are NOT Eurostat-published figures for a "
                    "unified entity — Eurostat has never published one. They are computed "
                    "here by summing the separately-reported Antwerpen (BE_0BEANR) and "
                    "Zeebrugge (BE_0BEZEE) figures for the same year/cargo_type/direction. "
                    "This summation is imperfect and unverified: (1) it assumes the two "
                    "ports' pre-2022 reporting methodologies were mutually compatible and "
                    "compatible with the post-2022 unified authority's methodology, which "
                    "cannot be checked — there is no independent pre-2022 unified figure to "
                    "compare against; (2) any intra-complex traffic that both ports "
                    "separately counted as 'goods handled' would be double-counted in the "
                    "sum, though this is unlikely for seaborne cargo statistics "
                    "specifically; (3) future Eurostat revisions to the legacy "
                    "BE_0BEANR/BE_0BEZEE figures would need to be manually re-summed, since "
                    "the derived rows have no independent source of their own."
                ),
                resolution=(
                    "Both the raw per-legacy-port rows (source=mar_mg_aa_pwhd / "
                    "mar_mg_am_pwhc) and these derived pre-2022 rows are retained in "
                    "port_throughput, so downstream consumers can choose the "
                    "source-faithful view (via merged_into_port_id) or the continuous "
                    f"derived view (via port_id={MERGED_PORT_CODE}) depending on their "
                    "need. Any headline Antwerp-Bruges continuity measure must use the "
                    "derived series explicitly, not by accident."
                ),
            )
        )

    return flags
