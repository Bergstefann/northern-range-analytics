from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from port_analytics.ingest.landing import land_raw_response


def test_land_raw_response_writes_timestamped_file(tmp_path: Path) -> None:
    payload = {"id": ["freq"], "value": {"0": 1}}
    pulled_at = datetime(2026, 8, 19, 14, 30, 0, tzinfo=UTC)

    out_path = land_raw_response("mar_mg_aa_pwhd", payload, tmp_path, pulled_at)

    assert out_path.name == "mar_mg_aa_pwhd_20260819T143000Z.json"
    assert out_path.parent == tmp_path
    assert json.loads(out_path.read_text(encoding="utf-8")) == payload


def test_land_raw_response_creates_landing_dir_if_missing(tmp_path: Path) -> None:
    landing_dir = tmp_path / "raw"
    pulled_at = datetime(2026, 8, 19, 14, 30, 0, tzinfo=UTC)

    out_path = land_raw_response("mar_mg_am_pwhc", {}, landing_dir, pulled_at)

    assert landing_dir.exists()
    assert out_path.exists()


def test_land_raw_response_does_not_overwrite_a_prior_landing(tmp_path: Path) -> None:
    payload_a = {"value": {"0": 1}}
    payload_b = {"value": {"0": 2}}

    path_a = land_raw_response(
        "mar_mg_aa_pwhd", payload_a, tmp_path, datetime(2026, 8, 19, 10, 0, 0, tzinfo=UTC)
    )
    path_b = land_raw_response(
        "mar_mg_aa_pwhd", payload_b, tmp_path, datetime(2026, 8, 19, 11, 0, 0, tzinfo=UTC)
    )

    assert path_a != path_b
    assert path_a.exists()
    assert path_b.exists()
    assert json.loads(path_a.read_text(encoding="utf-8")) == payload_a
    assert json.loads(path_b.read_text(encoding="utf-8")) == payload_b
