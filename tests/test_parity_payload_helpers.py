from __future__ import annotations

import pytest

from scripts.parity_payloads import gen_card, gen_music_score, gen_profile
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


def test_render_skill_detail_handles_effect_character_and_invalid_placeholders(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeMasterData:
        @staticmethod
        def character_by_id() -> dict[int, dict]:
            return {39: {"firstName": "初音", "givenName": "未来"}}

    monkeypatch.setattr(gen_card, "MD", FakeMasterData())
    skill = {
        "description": "{{1;v}}/{{1,2;v}}/{{1;c}}/{{9;v}}/{{bad;v}}/{{1;v;extra}}",
        "skillEffects": [
            {"id": 1, "skillEffectDetails": [{"activateEffectValue": 10}]},
            {"id": 2, "skillEffectDetails": [{"activateEffectValue": 5}]},
        ],
    }

    assert gen_card.render_skill_detail(skill, 39) == "10/15/初音未来/?/{{bad;v}}/{{1;v;extra}}"


def test_card_detail_event_keeps_last_attr_and_unique_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeMasterData:
        @staticmethod
        def event_id_by_card() -> dict[int, int]:
            return {7: 10}

        @staticmethod
        def event_by_id() -> dict[int, dict]:
            return {
                10: {
                    "id": 10,
                    "name": "Event",
                    "startAt": 100,
                    "aggregateAt": 200,
                    "assetbundleName": "event_bundle",
                }
            }

        @staticmethod
        def get(table: str) -> list[dict]:
            return {
                "eventDeckBonuses": [
                    {"eventId": 10, "cardAttr": "cute", "gameCharacterUnitId": 5},
                    {"eventId": 10, "cardAttr": "cool", "gameCharacterUnitId": 5},
                ],
                "gameCharacterUnits": [{"id": 5, "unit": "light_sound"}],
            }[table]

    monkeypatch.setattr(gen_card, "MD", FakeMasterData())
    monkeypatch.setattr(gen_card, "event_banner_character_id", lambda event_id: 39)

    event_info, asset_paths = gen_card._card_detail_event(7)

    assert event_info["bonus_attr"] == "cool"
    assert event_info["unit"] == "light_sound"
    assert event_info["banner_cid"] == 39
    assert asset_paths.keys() == {
        "event_attr_icon_path",
        "event_unit_icon_path",
        "event_chara_icon_path",
    }
    assert gen_card._card_detail_event(99) == (None, {})


def test_build_distribution_preserves_owned_character_and_attribute_ratios() -> None:
    items = [
        {"has_card": True, "card": {"character_id": 1, "attr": "cute"}},
        {"has_card": False, "card": {"character_id": 1, "attr": "cool"}},
        {"has_card": True, "card": {"character_id": 2, "attr": "unexpected"}},
    ]

    distribution = gen_card.build_distribution(
        items,
        icon_paths={1: "one.png", 2: "two.png"},
        color_codes={1: "#111111", 2: "#222222"},
        owned_data=True,
    )

    assert distribution["total_count"] == 3
    assert distribution["owned_count"] == 2
    assert distribution["max_character_bar_count"] == 1
    assert [stat["share"] for stat in distribution["character_stats"]] == [0.5, 0.5]
    stats_by_attr = {stat["attr"]: stat for stat in distribution["attribute_stats"]}
    assert stats_by_attr["cute"]["bar_count"] == 1
    assert stats_by_attr["cool"]["bar_count"] == 0
    assert stats_by_attr["unknown"]["bar_count"] == 1
    assert stats_by_attr["unknown"]["character_stats"][0]["icon_path"] == "two.png"
