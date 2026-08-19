from __future__ import annotations

from port_analytics.transform.jsonstat import decode_observations


def _payload() -> dict[str, object]:
    # Same shape verified against the real API: id order determines the
    # flat index's stride, with the last dimension (time) varying fastest.
    return {
        "id": ["freq", "unit", "direct", "rep_mar", "time"],
        "size": [1, 2, 3, 2, 2],
        "dimension": {
            "freq": {"category": {"index": {"A": 0}}},
            "unit": {"category": {"index": {"THS_T": 0, "RT_PRE": 1}}},
            "direct": {"category": {"index": {"TOTAL": 0, "IN": 1, "OUT": 2}}},
            "rep_mar": {"category": {"index": {"BE_0BEANR": 0, "DE_1DEHAM": 1}}},
            "time": {"category": {"index": {"2020": 0, "2021": 1}}},
        },
        # freq=A, unit=THS_T, direct=TOTAL fixed (all position 0):
        # index = rep_mar_pos * 2 + time_pos * 1
        "value": {
            "0": 206319,  # BE_0BEANR, 2020
            "1": 215852,  # BE_0BEANR, 2021
            "2": 109175,  # DE_1DEHAM, 2020
            "3": 111156,  # DE_1DEHAM, 2021
        },
    }


def test_decode_observations_maps_flat_index_back_to_category_codes() -> None:
    observations = decode_observations(_payload())

    by_port_year = {
        (obs.dimension_values["rep_mar"], obs.dimension_values["time"]): obs.value
        for obs in observations
    }

    assert by_port_year[("BE_0BEANR", "2020")] == 206319
    assert by_port_year[("BE_0BEANR", "2021")] == 215852
    assert by_port_year[("DE_1DEHAM", "2020")] == 109175
    assert by_port_year[("DE_1DEHAM", "2021")] == 111156


def test_decode_observations_sets_every_dimension_value() -> None:
    observations = decode_observations(_payload())

    for obs in observations:
        assert obs.dimension_values["freq"] == "A"
        assert obs.dimension_values["unit"] == "THS_T"
        assert obs.dimension_values["direct"] == "TOTAL"


def test_decode_observations_only_yields_present_entries() -> None:
    observations = decode_observations(_payload())

    assert len(observations) == 4


def test_decode_observations_handles_empty_value_map() -> None:
    payload = _payload()
    payload["value"] = {}

    assert decode_observations(payload) == []
