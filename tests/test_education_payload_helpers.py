from __future__ import annotations

import importlib
import sys

import pytest

from scripts.parity_payloads import common as parity_common

sys.modules.setdefault("common", parity_common)
_load_suite = parity_common.load_suite
parity_common.load_suite = lambda: {}
try:
    payloads = importlib.import_module("scripts.parity_payloads.gen_gacha_costume_vlive_edu")
finally:
    parity_common.load_suite = _load_suite


class _FakeMasterData:
    def __init__(self, tables: dict[str, list[dict]] | None = None):
        self._tables = tables or {}

    def get(self, table: str) -> list[dict]:
        return self._tables.get(table, [])


def test_area_item_filter_distinguishes_piapro_from_unit_items() -> None:
    item = {"areaId": 7}
    piapro_levels = [{"targetUnit": "piapro", "targetGameCharacterId": 21, "targetCardAttr": "cute"}]

    assert not payloads._area_item_matches_filter(item, piapro_levels, "idol", "", 0, False, False, False)
    assert payloads._area_item_matches_filter(item, piapro_levels, "", "", 0, False, False, True)
    assert payloads._area_item_matches_filter(item, piapro_levels, "", "", 21, False, False, False)
    assert payloads._area_item_matches_filter(item, piapro_levels, "", "cute", 0, False, False, False)
    assert payloads._area_item_matches_filter({"areaId": 11}, [{}], "", "", 0, True, False, False)
    assert payloads._area_item_matches_filter({"areaId": 13}, [{}], "", "", 0, False, True, False)


def test_area_shop_items_preserve_explicit_mapping_before_sequence_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    levels = [{"areaItemId": item_id, "level": level} for item_id in (1, 2) for level in (1, 2)]
    fallback_shops = [{"id": 20 + seq, "shopId": 6, "seq": seq, "startAt": 0} for seq in range(4)]
    monkeypatch.setattr(
        payloads,
        "MD",
        _FakeMasterData(
            {
                "areaItems": [
                    {"id": 1, "areaId": 7},
                    {"id": 2, "areaId": 7},
                ],
                "areaItemLevels": levels,
                "shopItems": [{"id": 10, "resourceBoxId": 100, "startAt": 0}, *fallback_shops],
                "resourceBoxes": [
                    {
                        "id": 100,
                        "resourceBoxPurpose": "shop_item",
                        "details": [{"resourceType": "area_item", "resourceId": 1, "resourceLevel": 1}],
                    }
                ],
            }
        ),
    )

    result = payloads._area_shop_items([1, 2], now_ms=1_000)

    assert result[1][1]["id"] == 10
    assert result[1][2]["id"] == 21
    assert result[2][1]["id"] == 22
    assert result[2][2]["id"] == 23


def test_challenge_reward_totals_skip_claimed_and_missing_boxes() -> None:
    rewards = [
        {"id": 1, "resourceBoxId": 10},
        {"id": 2, "resourceBoxId": 20},
        {"id": 3, "resourceBoxId": 99},
    ]
    boxes = {
        10: {"details": [{"resourceType": "jewel", "resourceQuantity": 50}]},
        20: {
            "details": [
                {"resourceType": "material", "resourceId": 15, "resourceQuantity": 3},
                {"resourceType": "material", "resourceId": 16, "resourceQuantity": 8},
            ]
        },
    }

    assert payloads._challenge_reward_totals(rewards, {1}, boxes) == (0, 3)


def test_power_bonus_helpers_apply_caps_and_all_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        payloads,
        "MD",
        _FakeMasterData(
            {
                "areaItemLevels": [
                    {
                        "areaItemId": 1,
                        "level": 2,
                        "power1BonusRate": 1.5,
                        "targetGameCharacterId": 1,
                        "targetUnit": "more_more_jump",
                        "targetCardAttr": "cute",
                    }
                ],
                "characterRanks": [{"characterId": 1, "characterRank": 5, "power1BonusRate": 2.0}],
                "mysekaiGateLevels": [{"mysekaiGateId": 2, "level": 3, "powerBonusRate": 4.0}],
            }
        ),
    )
    monkeypatch.setattr(
        payloads,
        "SUITE",
        {
            "userAreas": [{"areaItems": [{"areaItemId": 1, "level": 3}]}],
            "userCharacters": [{"characterId": 1, "characterRank": 5}],
            "userMysekaiFixtureGameCharacterPerformanceBonuses": [{"gameCharacterId": 1, "totalBonusRate": 30.0}],
            "userMysekaiGates": [{"mysekaiGateId": 2, "mysekaiGateLevel": 3}],
        },
    )
    chara, unit, attr = payloads._empty_power_bonuses()

    payloads._apply_area_item_power_bonus({1: 2}, chara, unit, attr)
    payloads._apply_character_power_bonuses(chara)
    payloads._apply_gate_power_bonuses(unit)

    assert chara[1] == {"area_item": 1.5, "rank": 2.0, "fixture": 3.0}
    assert unit["idol"] == {"area_item": 1.5, "gate": 4.0}
    assert unit["piapro"]["gate"] == 4.0
    assert attr["cute"]["area_item"] == 1.5


def test_area_item_level_infos_accumulate_material_requirements(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(payloads, "_material_icon", lambda resource_type, resource_id: f"{resource_type}:{resource_id}")
    levels = [
        {"level": 1, "power1BonusRate": 1.0},
        {"level": 2, "power1BonusRate": 2.0},
        {"level": 3, "power1BonusRate": 3.0},
    ]
    shop_levels = {
        2: {"costs": [{"cost": {"resourceType": "coin", "resourceId": 0, "quantity": 20}}]},
        3: {"costs": [{"cost": {"resourceType": "coin", "resourceId": 0, "quantity": 30}}]},
    }

    rows = payloads._area_item_level_infos(
        levels,
        shop_levels,
        current=1,
        min_current=0,
        max_visible=3,
        materials={payloads.AREA_COIN_MATERIAL_ID: 40},
    )

    assert rows[0]["materials"] == []
    assert rows[1]["materials"][0]["sum_quantity"] == 20
    assert rows[1]["can_upgrade"]
    assert rows[2]["materials"][0]["sum_quantity"] == 50
    assert not rows[2]["can_upgrade"]


def test_bond_helpers_resolve_style_color_and_remaining_exp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(payloads.ASSETS, "chara_icon", lambda character_id: f"chara/{character_id}")
    styles = {
        101: {"character_id": 1, "color_code": "#112233"},
        102: {"character_id": 2, "color_code": "invalid"},
    }

    info = payloads._bond_info(
        {"rank": 2, "exp": 10},
        (101, 102),
        styles,
        {1: 20, 2: 30},
        {2: 100, 3: 160},
        max_level=3,
    )

    assert info["chara_icon_path1"] == "chara/1"
    assert info["chara_rank2"] == 30
    assert info["color1"] == [17, 34, 51]
    assert info["color2"] == [100, 100, 100]
    assert info["need_exp"] == 50


def test_leader_helpers_use_legacy_fallback_and_ex_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        payloads,
        "SUITE",
        {
            "userCharacterMissionV2s": [{"characterId": 2, "characterMissionType": "play_live_ex", "progress": 7}],
            "userCharacterLiveUsageCounts": [{"characterId": 1, "characterLiveUsageType": "leader", "usageCount": 9}],
            "userCharacterMissionV2Statuses": [{"characterId": 2, "parameterGroupId": 101, "seq": 2}],
        },
    )
    monkeypatch.setattr(
        payloads,
        "MD",
        _FakeMasterData(
            {
                "characterMissionV2ParameterGroups": [
                    {"id": 101, "seq": 1, "requirement": 10},
                    {"id": 101, "seq": 2, "requirement": 20},
                ]
            }
        ),
    )

    play_count, ex_count, has_ex, has_play = payloads._leader_mission_progress()
    payloads._fallback_leader_play_counts(play_count, has_play)
    ex_level = payloads._leader_ex_statuses(ex_count, has_ex)

    assert play_count == {1: 9}
    assert ex_count == {2: 27}
    assert ex_level == {2: 3}


def test_mission_current_preserves_ex_cleared_round_arithmetic() -> None:
    groups = [
        {"seq": 1, "requirement": 10},
        {"seq": 2, "requirement": 20},
    ]

    assert payloads._mission_current(groups, current=5, received=2, is_ex=True) == 35
    assert payloads._mission_current(groups, current=40, received=2, is_ex=True) == 40
    assert payloads._mission_current(groups, current=0, received=2, is_ex=True) == 30
    assert payloads._mission_current(groups, current=0, received=2, is_ex=False) == 20


def test_final_character_level_uses_total_exp_and_pending_rewards() -> None:
    char_levels = [(1, 0), (2, 100), (3, 250)]

    assert payloads._final_character_level(
        current_level=2,
        current_exp=20,
        current_total_exp=0,
        pending_exp=160,
        char_levels=char_levels,
        level_total=dict(char_levels),
    ) == (3, 30)
    assert payloads._final_character_level(2, 20, 0, 5, [], {}) == (2, 25)


def test_mission_rows_combine_progress_status_and_pending_exp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        payloads,
        "MD",
        _FakeMasterData(
            {
                "levels": [
                    {"levelType": "character", "level": 1, "totalExp": 0},
                    {"levelType": "character", "level": 2, "totalExp": 100},
                    {"levelType": "character", "level": 3, "totalExp": 250},
                ],
                "characterMissionV2s": [
                    {"id": 1, "characterId": 6, "characterMissionType": "play_live", "parameterGroupId": 1},
                    {"id": 2, "characterId": 6, "characterMissionType": "play_live_ex", "parameterGroupId": 101},
                ],
                "characterMissionV2ParameterGroups": [
                    {"id": 1, "seq": 1, "requirement": 10, "exp": 5},
                    {"id": 1, "seq": 2, "requirement": 20, "exp": 7},
                    {"id": 101, "seq": 1, "requirement": 10, "exp": 2},
                    {"id": 101, "seq": 2, "requirement": 20, "exp": 3},
                ],
            }
        ),
    )
    monkeypatch.setattr(
        payloads,
        "SUITE",
        {
            "userCharacters": [{"characterId": 6, "characterRank": 2, "exp": 9, "totalExp": 120}],
            "userCharacterMissionV2s": [
                {"characterId": 6, "characterMissionType": "play_live", "progress": 15},
                {"characterId": 6, "characterMissionType": "play_live_ex", "progress": 3},
            ],
            "userCharacterMissionV2Statuses": [
                {
                    "characterId": 6,
                    "missionId": 1,
                    "parameterGroupId": 1,
                    "seq": 1,
                    "missionStatus": "achieved",
                },
                {"characterId": 6, "missionId": 2, "parameterGroupId": 101, "seq": 1},
            ],
        },
    )

    rows, current_level, current_exp, pending_exp, final_level, final_exp = payloads._mission_rows(6)

    assert (current_level, current_exp, pending_exp, final_level, final_exp) == (2, 20, 5, 2, 25)
    assert rows[0]["current"] == 15
    assert rows[0]["next_need"] == 20
    assert rows[1]["current"] == 13
    assert rows[1]["current_round"] == 2
    assert rows[1]["current_round_progress"] == 3
    assert rows[1]["next_need"] == 30


def test_mission_section_builds_standard_and_ex_display_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        payloads,
        "MD",
        _FakeMasterData(
            {
                "characterMissionV2s": [
                    {"id": 1, "characterId": 6, "characterMissionType": "play_live", "parameterGroupId": 1},
                    {"id": 2, "characterId": 6, "characterMissionType": "play_live_ex", "parameterGroupId": 101},
                ],
                "characterMissionV2ParameterGroups": [
                    {"id": 1, "seq": 1, "requirement": 10, "exp": 2},
                    {"id": 1, "seq": 2, "requirement": 20, "exp": 3},
                    {"id": 101, "seq": 1, "requirement": 10, "exp": 2},
                    {"id": 101, "seq": 2, "requirement": 20, "exp": 3},
                ],
            }
        ),
    )
    standard = payloads._mission_section(
        6,
        {
            "mission_type": "play_live",
            "title": "standard",
            "is_ex": False,
            "current": 15,
            "ratio": 0.75,
            "upper": 20,
        },
    )
    ex = payloads._mission_section(
        6,
        {
            "mission_type": "play_live_ex",
            "title": "ex",
            "is_ex": True,
            "current": 13,
            "ratio": 0.1,
            "current_round": 2,
            "current_round_progress": 3,
        },
    )

    assert standard["reached_seq"] == 1
    assert standard["display_rows"][1]["acc_exp"] == 5
    assert standard["upper"] == 20
    assert ex["reached_seq"] == 2
    assert ex["display_rows"][1]["acc_requirement"] == 30
    assert ex["current_round_no"] == 2
