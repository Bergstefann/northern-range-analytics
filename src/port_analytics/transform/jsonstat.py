"""Pure JSON-stat 2.0 decoding — no knowledge of ports, cargo, or Eurostat
specifically. Turns the sparse, flat-indexed `value` map into a list of
observations keyed by each dimension's actual category code.

`value` is keyed by a flat, row-major index: with `id` giving dimension
order and `size` giving each dimension's category count, the last
dimension in `id` varies fastest. A key's absence from `value` means
missing data — there is no explicit null. See docs/data-quality-notes.md.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class JsonStatObservation(BaseModel):
    dimension_values: dict[str, str]
    value: float


def decode_observations(payload: dict[str, Any]) -> list[JsonStatObservation]:
    dimension_ids: list[str] = payload["id"]
    sizes: list[int] = payload["size"]
    dimensions: dict[str, Any] = payload["dimension"]
    value_map: dict[str, float] = payload["value"]

    codes_by_dimension: dict[str, list[str]] = {}
    for dim_id in dimension_ids:
        index_map: dict[str, int] = dimensions[dim_id]["category"]["index"]
        ordered = sorted(index_map.items(), key=lambda item: item[1])
        codes_by_dimension[dim_id] = [code for code, _position in ordered]

    strides = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]

    observations: list[JsonStatObservation] = []
    for flat_index_str, value in value_map.items():
        remainder = int(flat_index_str)
        dimension_values: dict[str, str] = {}
        for dim_id, stride in zip(dimension_ids, strides, strict=True):
            position, remainder = remainder // stride, remainder % stride
            dimension_values[dim_id] = codes_by_dimension[dim_id][position]
        observations.append(
            JsonStatObservation(dimension_values=dimension_values, value=float(value))
        )

    return observations
