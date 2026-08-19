from __future__ import annotations

from typing import Any

import pytest
import requests

from port_analytics.ingest.eurostat_client import EurostatAPIError, fetch_dataset

VALID_PAYLOAD: dict[str, Any] = {
    "version": "2.0",
    "class": "dataset",
    "label": "Gross weight of goods handled in the top 20 EU ports by direction",
    "source": "ESTAT",
    "updated": "2025-12-03T23:00:00+0100",
    "id": ["freq", "unit", "direct", "rep_mar", "time"],
    "size": [1, 2, 3, 2, 2],
    "dimension": {
        "freq": {
            "label": "Time frequency",
            "category": {"index": {"A": 0}, "label": {"A": "Annual"}},
        },
    },
    "value": {"0": 206319, "1": 215852, "2": 109175, "3": 111156},
    "extension": {"lang": "EN"},
}


class FakeResponse:
    def __init__(
        self, status_code: int, payload: Any = None, raise_json_error: bool = False
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self._raise_json_error = raise_json_error

    def json(self) -> Any:
        if self._raise_json_error:
            raise ValueError("not valid json")
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, *, params: dict[str, object], timeout: float) -> FakeResponse:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return self._response


def test_fetch_dataset_returns_raw_payload_on_success() -> None:
    session = FakeSession(FakeResponse(200, VALID_PAYLOAD))

    result = fetch_dataset("mar_mg_aa_pwhd", ["BE_0BEANR", "DE_1DEHAM"], session)

    assert result == VALID_PAYLOAD
    assert session.calls[0]["params"]["rep_mar"] == ["BE_0BEANR", "DE_1DEHAM"]
    assert session.calls[0]["url"].endswith("/mar_mg_aa_pwhd")


def test_fetch_dataset_raises_loudly_on_connection_failure() -> None:
    class BrokenSession:
        def get(self, url: str, *, params: dict[str, object], timeout: float) -> FakeResponse:
            raise requests.ConnectionError("no route to host")

    with pytest.raises(EurostatAPIError, match="request to Eurostat failed"):
        fetch_dataset("mar_mg_aa_pwhd", ["BE_0BEANR"], BrokenSession())


def test_fetch_dataset_raises_loudly_on_http_error() -> None:
    session = FakeSession(FakeResponse(404))

    with pytest.raises(EurostatAPIError, match="404"):
        fetch_dataset("does_not_exist", ["BE_0BEANR"], session)


def test_fetch_dataset_raises_loudly_on_invalid_json() -> None:
    session = FakeSession(FakeResponse(200, raise_json_error=True))

    with pytest.raises(EurostatAPIError, match="not valid JSON"):
        fetch_dataset("mar_mg_aa_pwhd", ["BE_0BEANR"], session)


def test_fetch_dataset_raises_loudly_on_malformed_shape() -> None:
    malformed = {"unexpected": "shape"}
    session = FakeSession(FakeResponse(200, malformed))

    with pytest.raises(EurostatAPIError, match="expected JSON-stat shape"):
        fetch_dataset("mar_mg_aa_pwhd", ["BE_0BEANR"], session)


def test_fetch_dataset_raises_loudly_on_empty_dataset() -> None:
    empty = {**VALID_PAYLOAD, "value": {}}
    session = FakeSession(FakeResponse(200, empty))

    with pytest.raises(EurostatAPIError, match="zero data points"):
        fetch_dataset("mar_mg_aa_pwhd", ["BE_0BEANR"], session)
