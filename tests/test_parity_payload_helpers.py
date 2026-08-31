from __future__ import annotations

import pytest

from scripts.parity_payloads import gen_music_score, gen_profile
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


def _omakase_meta(difficulty: str, value: float) -> dict:
    return {
        "difficulty": difficulty,
        "music_time": value,
        "event_rate": value,
        "base_score": value,
        "base_score_auto": value,
        "fever_score": value,
        "fever_end_time": value,
        "tap_count": value,
        "skill_score_solo": [value] * 6,
        "skill_score_auto": [value] * 6,
        "skill_score_multi": [value] * 6,
    }


def test_inject_omakase_averages_supported_difficulties_in_place() -> None:
    metas = [
        _omakase_meta("master", 1.0),
        _omakase_meta("expert", 2.0),
        _omakase_meta("hard", 4.0),
        _omakase_meta("easy", 100.0),
    ]

    result = gen_music_score._inject_omakase(metas)

    assert result is metas
    generated = result[-3:]
    assert [item["difficulty"] for item in generated] == ["master", "expert", "hard"]
    assert all(item["music_id"] == 10000 for item in generated)
    assert all(item["music_time"] == pytest.approx(7 / 3) for item in generated)
    assert all(item["event_rate"] == 2.0 for item in generated)
    assert all(item["tap_count"] == 2.0 for item in generated)
    assert all(item["skill_score_solo"] == pytest.approx([7 / 3] * 6) for item in generated)


def test_inject_omakase_preserves_existing_or_empty_input() -> None:
    existing = [{"music_id": 10000, "difficulty": "master"}]
    unsupported = [_omakase_meta("easy", 1.0)]

    assert gen_music_score._inject_omakase(existing) is existing
    assert gen_music_score._inject_omakase(unsupported) == unsupported


def test_suite_music_results_merges_flat_and_nested_results(monkeypatch: pytest.MonkeyPatch) -> None:
    suite = {
        "userMusicResults": [
            {"musicId": 1, "musicDifficultyType": "master", "fullComboFlg": True},
            {"musicId": 2, "musicDifficulty": "expert", "playResult": "not_clear"},
        ],
        "userMusics": [
            {
                "musicId": 1,
                "userMusicDifficultyStatuses": [
                    {
                        "musicDifficulty": "master",
                        "userMusicResults": [
                            {"fullPerfectFlg": True},
                            {"musicId": 3, "musicDifficultyType": "hard", "playResult": "clear"},
                        ],
                    }
                ],
            }
        ],
    }
    monkeypatch.setattr(gen_music_score.common, "load_suite", lambda: suite)
    gen_music_score._suite_music_results.cache_clear()

    assert gen_music_score._suite_music_results() == {
        "master": {1: "ap"},
        "expert": {2: "not_clear"},
        "hard": {3: "clear"},
    }

    gen_music_score._suite_music_results.cache_clear()


@pytest.mark.parametrize(
    ("requested", "expected_level"),
    [
        (3, 3),
        (4, 3),
        (0, 1),
    ],
)
def test_resolve_level_visual_selects_expected_usable_level(requested: int, expected_level: int) -> None:
    levels = [
        {"level": 0},
        {"level": 1, "assetbundleName": "first"},
        {"level": 3, "honorRarity": "high"},
        {"level": 5, "assetbundleName": "last"},
    ]

    assert gen_profile._resolve_level_visual(levels, requested)["level"] == expected_level


def test_resolve_level_visual_returns_none_without_usable_levels() -> None:
    assert gen_profile._resolve_level_visual([{"level": 1}], 1) is None
