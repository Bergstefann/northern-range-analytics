"""Orchestrates the full raw -> domain transform: both datasets, plus the
Antwerp-Bruges continuity derivation on top. This is what Phase 3's
loader calls."""

from __future__ import annotations

from typing import Any

from port_analytics.models import DataQualityFlag, PortThroughputRow
from port_analytics.transform.continuity import derive_pre_merger_antwerp_bruges
from port_analytics.transform.throughput import build_cargo_rows, build_direction_rows


def transform_all(
    direction_payload: dict[str, Any],
    cargo_payload: dict[str, Any],
) -> tuple[list[PortThroughputRow], list[DataQualityFlag]]:
    direction_rows, direction_flags = build_direction_rows(direction_payload)
    cargo_rows, cargo_flags = build_cargo_rows(cargo_payload)

    reported_rows = direction_rows + cargo_rows
    derived_rows, merger_flags = derive_pre_merger_antwerp_bruges(reported_rows)

    rows = reported_rows + derived_rows
    flags = direction_flags + cargo_flags + merger_flags
    return rows, flags
