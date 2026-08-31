import pytest

from scripts.parity_payloads.common import normalize_extended_json
from scripts.parity_payloads.gen_sk_misc_stamp import count_csb_stop_texts


def test_normalize_extended_json_converts_nested_mongo_wrappers():
    value = {
        "integer": {"$numberLong": "42"},
        "small_integer": {"$numberInt": "7"},
        "decimal": {"$numberDouble": "1.25"},
        "identifier": {"$oid": 123},
        "timestamp": {"$date": {"$numberLong": "1600000000000"}},
        "items": [{"$numberInt": "9"}, {"nested": {"$oid": "abc"}}],
    }

    assert normalize_extended_json(value) == {
        "integer": 42,
        "small_integer": 7,
        "decimal": 1.25,
        "identifier": "123",
        "timestamp": 1_600_000_000_000,
        "items": [9, {"nested": "abc"}],
    }


def test_normalize_extended_json_preserves_unknown_wrapper_and_scalar():
    assert normalize_extended_json({"$unknown": {"$numberInt": "3"}}) == {"$unknown": 3}
    assert normalize_extended_json("plain") == "plain"


@pytest.mark.parametrize(
    ("points", "expected"),
    [
        ([], 1),
        ([{"rank": 10, "time": 0, "score": 100}], 1),
        (
            [
                {"rank": 10, "time": 300_000, "score": 100},
                {"rank": 10, "time": 0, "score": 100},
                {"rank": 10, "time": 60_000, "score": 100},
                {"rank": 10, "time": 120_000, "score": 100},
                {"rank": 10, "time": 180_000, "score": 100},
                {"rank": 10, "time": 240_000, "score": 100},
            ],
            2,
        ),
        (
            [
                {"rank": 10, "time": 0, "score": 100},
                {"rank": 10, "time": 60_000, "score": 100},
                {"rank": 10, "time": 120_000, "score": 200},
                {"rank": 101, "time": 180_000, "score": 200},
                {"rank": 10, "time": 300_001, "score": 200},
            ],
            1,
        ),
    ],
)
def test_count_csb_stop_texts_matches_segment_rules(points, expected):
    assert count_csb_stop_texts(points) == expected
