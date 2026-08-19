"""Raw landing layer: writes Eurostat responses to disk exactly as
received, before any cleaning touches them."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def land_raw_response(
    dataset_code: str,
    payload: dict[str, Any],
    landing_dir: Path,
    pulled_at: datetime,
) -> Path:
    """Write a raw Eurostat response to disk as-is.

    Filenames are timestamped so re-running the pull never overwrites a
    prior landing — the raw layer is an append-only audit trail.
    """
    landing_dir.mkdir(parents=True, exist_ok=True)
    timestamp = pulled_at.strftime("%Y%m%dT%H%M%SZ")
    out_path = landing_dir / f"{dataset_code}_{timestamp}.json"
    out_path.write_text(json.dumps(payload), encoding="utf-8")
    return out_path
