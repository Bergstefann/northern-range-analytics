"""Domain models for the target relational schema (docs/data-project-build-spec.md
§4). These are in-memory representations produced by the transform layer;
Phase 3's loader maps them onto real Azure SQL surrogate keys.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class Direction(StrEnum):
    TOTAL = "total"
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class FlagType(StrEnum):
    """Mirrors docs/data-project-build-spec.md §4, minus suppressed_confidential
    (dropped — see docs/data-quality-notes.md, 'Finding 4')."""

    MISSING_YEAR = "missing_year"
    UNIT_MISMATCH = "unit_mismatch"
    REVISED_ESTIMATE = "revised_estimate"
    PORT_MERGER = "port_merger"
    CODE_CHANGE = "code_change"
    OUTLIER_SUSPECTED = "outlier_suspected"


class Port(BaseModel):
    eurostat_code: str
    port_name: str
    country_code: str
    un_locode: str | None = None
    merged_into: str | None = None
    """eurostat_code of the port this one merged into, or None."""


class CargoType(BaseModel):
    cargo_type_code: str
    cargo_type_name: str


class ThroughputRef(BaseModel):
    """Natural key identifying one port_throughput row (or the row that
    *would* exist, for flags about missing data — see DataQualityFlag)."""

    port_code: str
    cargo_type_code: str
    year: int
    direction: Direction


class PortThroughputRow(BaseModel):
    port_code: str
    cargo_type_code: str
    year: int
    direction: Direction
    gross_weight_tonnes: float
    source: str
    """Eurostat dataset code this row came from, or 'derived_sum:...' for a
    computed row (see transform/continuity.py)."""


class DataQualityFlag(BaseModel):
    flag_type: FlagType
    description: str
    resolution: str
    port_code: str | None = None
    throughput_ref: ThroughputRef | None = None
