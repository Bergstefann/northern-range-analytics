from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from port_analytics.config import DATASET_CODES, PORT_CODES
from port_analytics.ingest.eurostat_client import EurostatAPIError
from port_analytics.ingest.pull import pull_and_land


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.status_code = 200
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


def _fixture_payload(dataset_code: str) -> dict[str, Any]:
    return {
        "version": "2.0",
        "class": "dataset",
        "label": f"fixture for {dataset_code}",
        "source": "ESTAT",
        "updated": "2025-12-03T23:00:00+0100",
        "id": ["freq", "rep_mar", "time"],
        "size": [1, 1, 1],
        "dimension": {},
        "value": {"0": 1.0},
        "extension": {},
    }


class FakeSession:
    def __init__(self) -> None:
        self.requested_datasets: list[str] = []
        self.requested_params: list[dict[str, object]] = []

    def get(self, url: str, *, params: dict[str, object], timeout: float) -> FakeResponse:
        dataset_code = url.rsplit("/", 1)[-1]
        self.requested_datasets.append(dataset_code)
        self.requested_params.append(params)
        return FakeResponse(_fixture_payload(dataset_code))


def test_pull_and_land_pulls_both_datasets_and_lands_them(tmp_path: Path) -> None:
    session = FakeSession()

    landed = pull_and_land(landing_dir=tmp_path, session=session)

    assert session.requested_datasets == list(DATASET_CODES.values())
    assert len(landed) == 2
    for path in landed:
        assert path.exists()
        content = json.loads(path.read_text(encoding="utf-8"))
        assert content["value"] == {"0": 1.0}


def test_pull_and_land_filters_to_target_ports(tmp_path: Path) -> None:
    session = FakeSession()

    pull_and_land(landing_dir=tmp_path, session=session)

    for params in session.requested_params:
        assert params["rep_mar"] == PORT_CODES


def test_pull_and_land_propagates_errors_without_swallowing(tmp_path: Path) -> None:
    class FailingSession:
        def get(self, url: str, *, params: dict[str, object], timeout: float) -> FakeResponse:
            return FakeResponse({"unexpected": "shape"})

    with pytest.raises(EurostatAPIError):
        pull_and_land(landing_dir=tmp_path, session=FailingSession())
