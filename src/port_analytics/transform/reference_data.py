"""Static reference data mapping Eurostat's own codes onto the domain
model. See docs/data-quality-notes.md for the investigation behind these
codes and the port-merger cutover dates.
"""

from __future__ import annotations

from port_analytics.models import CargoType, Direction, Port

PORTS: dict[str, Port] = {
    "BE_0BE003": Port(eurostat_code="BE_0BE003", port_name="Antwerp-Bruges", country_code="BE"),
    "BE_0BEANR": Port(
        eurostat_code="BE_0BEANR",
        port_name="Antwerpen",
        country_code="BE",
        merged_into="BE_0BE003",
    ),
    "BE_0BEZEE": Port(
        eurostat_code="BE_0BEZEE",
        port_name="Zeebrugge",
        country_code="BE",
        merged_into="BE_0BE003",
    ),
    "DE_1DEHAM": Port(eurostat_code="DE_1DEHAM", port_name="Hamburg", country_code="DE"),
    "NL_0NLRTM": Port(eurostat_code="NL_0NLRTM", port_name="Rotterdam", country_code="NL"),
    "PL_0PLGDN": Port(eurostat_code="PL_0PLGDN", port_name="Gdansk", country_code="PL"),
}

# A synthetic "Total" cargo type is needed because mar_mg_aa_pwhd (the
# direction dataset) has no cargo breakdown at all — every row represents
# all cargo combined. Chosen over a nullable cargo_type_id to keep the FK
# non-nullable, matching the spec's convention of marking only
# merged_into_port_id as nullable. See docs/data-quality-notes.md.
#
# 'UNK' (Unknown) is deliberately excluded: it has zero data points across
# every port and year in mar_mg_am_pwhc (Finding 2) — a phantom category,
# not a real one.
CARGO_TYPES: dict[str, CargoType] = {
    "TOTAL": CargoType(cargo_type_code="TOTAL", cargo_type_name="Total (all cargo)"),
    "LBK": CargoType(cargo_type_code="LBK", cargo_type_name="Liquid bulk"),
    "DBK": CargoType(cargo_type_code="DBK", cargo_type_name="Dry bulk"),
    "LCNT": CargoType(cargo_type_code="LCNT", cargo_type_name="Containers"),
    "RO_MSP": CargoType(cargo_type_code="RO_MSP", cargo_type_name="Ro-Ro (self-propelled)"),
    "RO_MNSP": CargoType(cargo_type_code="RO_MNSP", cargo_type_name="Ro-Ro (non-self-propelled)"),
    "OTH": CargoType(cargo_type_code="OTH", cargo_type_name="Other"),
}

DIRECTION_CODES: dict[str, Direction] = {
    "TOTAL": Direction.TOTAL,
    "IN": Direction.INBOUND,
    "OUT": Direction.OUTBOUND,
}

# First year the merged entity (BE_0BE003) reports; legacy codes
# (BE_0BEANR, BE_0BEZEE) report through the year before this, with no
# overlap year. See docs/data-quality-notes.md, 'Finding 1'.
ANTWERP_BRUGES_MERGER_YEAR = 2022
