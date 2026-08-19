"""Phase 1 entrypoint: pull both target Eurostat datasets for the
Northern Range ports and land them raw. Run directly with:

    python -m port_analytics.ingest.pull
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import requests

from port_analytics.config import DATASET_CODES, DEFAULT_LANDING_DIR, PORT_CODES
from port_analytics.ingest.eurostat_client import HttpGetter, fetch_dataset
from port_analytics.ingest.landing import land_raw_response


def pull_and_land(
    landing_dir: Path = DEFAULT_LANDING_DIR,
    session: HttpGetter | None = None,
) -> list[Path]:
    """Pull both target Eurostat datasets for the five Northern Range ports and land them raw."""
    http = session if session is not None else requests.Session()
    landed: list[Path] = []
    for dataset_code in DATASET_CODES.values():
        payload = fetch_dataset(dataset_code, PORT_CODES, http)
        landed.append(land_raw_response(dataset_code, payload, landing_dir, datetime.now(UTC)))
    return landed


if __name__ == "__main__":
    for path in pull_and_land():
        print(f"landed {path}")
