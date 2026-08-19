"""Adapter that talks to the Eurostat REST API — the only part of this
project allowed to know about HTTP, JSON-stat, or Eurostat's URL shape.

`fetch_dataset` takes an injectable `session` (anything with a `.get`
matching `HttpGetter`) so it's testable without network access: tests pass
a fake session, real callers pass a `requests.Session`.
"""

from __future__ import annotations

from typing import Any, Protocol

import requests
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from port_analytics.config import EUROSTAT_BASE_URL


class EurostatAPIError(RuntimeError):
    """Raised when the Eurostat API returns something Phase 1 can't land."""


class JsonStatDataset(BaseModel):
    """Structural shape of a Eurostat JSON-stat 2.0 dataset response.

    Used only to validate that a response looks like real JSON-stat data
    before landing it. The raw dict actually landed is the untouched
    parsed payload, not a re-serialization of this model — a pydantic
    round-trip would coerce ints to floats in `value` and silently drop
    unmodelled fields, which is not "as-is".
    """

    model_config = ConfigDict(populate_by_name=True)

    version: str
    class_: str = Field(alias="class")
    label: str
    source: str
    updated: str
    id: list[str]
    size: list[int]
    dimension: dict[str, Any]
    value: dict[str, float]
    extension: dict[str, Any] = Field(default_factory=dict)


class HttpGetter(Protocol):
    def get(self, url: str, *, params: dict[str, Any], timeout: float) -> requests.Response: ...


def fetch_dataset(
    dataset_code: str,
    rep_mar_codes: list[str],
    session: HttpGetter,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Pull one Eurostat dataset in JSON-stat format, filtered to the given ports.

    Returns the untouched parsed response body. Raises EurostatAPIError on
    any HTTP failure, malformed payload, or a dataset with zero data
    points — this must fail loudly rather than land something that looks
    like data but isn't.
    """
    url = f"{EUROSTAT_BASE_URL}/{dataset_code}"
    params: dict[str, Any] = {"format": "JSON", "lang": "EN", "rep_mar": rep_mar_codes}

    try:
        response = session.get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise EurostatAPIError(f"request to Eurostat failed for {dataset_code}: {exc}") from exc

    if response.status_code != 200:
        raise EurostatAPIError(f"Eurostat returned HTTP {response.status_code} for {dataset_code}")

    try:
        payload: dict[str, Any] = response.json()
    except ValueError as exc:
        raise EurostatAPIError(f"Eurostat response for {dataset_code} was not valid JSON") from exc

    try:
        validated = JsonStatDataset.model_validate(payload)
    except ValidationError as exc:
        raise EurostatAPIError(
            f"Eurostat response for {dataset_code} did not match the expected "
            f"JSON-stat shape: {exc}"
        ) from exc

    if not validated.value:
        raise EurostatAPIError(f"Eurostat returned zero data points for {dataset_code}")

    return payload
