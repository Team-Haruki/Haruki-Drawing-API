import json

import pytest

from scripts.build_card_list_payload import build_payload


def _write_master(master_dir, filename: str, items: list[dict]) -> None:
    (master_dir / filename).write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def _write_minimal_master(master_dir) -> None:
    _write_master(
        master_dir,
        "cards.json",
        [
            {
                "id": 101,
                "characterId": 1,
                "cardRarityType": "rarity_4",
                "specialTrainingPower1BonusFixed": 100,
                "specialTrainingPower2BonusFixed": 200,
                "specialTrainingPower3BonusFixed": 300,
                "attr": "cute",
                "supportUnit": "none",
                "skillId": 11,
                "cardSkillName": "Skill One",
                "specialTrainingSkillId": 12,
                "specialTrainingSkillName": "Skill Two",
                "prefix": "Four Star",
                "assetbundleName": "res001_no101",
                "releaseAt": 1700000000000,
                "cardSupplyId": 3,
                "initialSpecialTrainingStatus": "not_do",
                "cardParameters": [
                    {"cardParameterType": "param1", "power": 10},
                    {"cardParameterType": "param1", "power": 20},
                    {"cardParameterType": "param2", "power": 30},
                    {"cardParameterType": "param3", "power": 40},
                ],
            },
            {
                "id": 102,
                "characterId": 27,
                "cardRarityType": "rarity_birthday",
                "attr": "happy",
                "supportUnit": "light_sound",
                "skillId": 11,
                "cardSkillName": "Birthday Skill",
                "prefix": "Birthday",
                "assetbundleName": "res027_no102",
                "releaseAt": 1700000100000,
                "cardSupplyId": 1,
                "cardParameters": [],
            },
        ],
    )
    _write_master(
        master_dir,
        "gameCharacters.json",
        [
            {"id": 1, "firstName": "星乃", "givenName": "一歌", "unit": "light_sound"},
            {"id": 27, "firstName": "初音", "givenName": "ミク", "unit": "piapro"},
        ],
    )
    _write_master(
        master_dir,
        "skills.json",
        [
            {
                "id": 11,
                "shortDescription": "score",
                "description": "{{1;d}}秒間 スコアが{{1;v}}%UPする",
                "descriptionSpriteName": "score_up",
                "skillEffects": [
                    {
                        "id": 1,
                        "skillEffectDetails": [
                            {"activateEffectDuration": 5.0, "activateEffectValue": 20},
                            {"activateEffectDuration": 5.0, "activateEffectValue": 40},
                        ],
                    }
                ],
            },
            {
                "id": 12,
                "shortDescription": "life",
                "description": "ライフ回復",
                "descriptionSpriteName": "life_recovery",
                "skillEffects": [],
            },
        ],
    )
    _write_master(
        master_dir,
        "cardSupplies.json",
        [
            {"id": 1, "cardSupplyType": "normal"},
            {"id": 3, "cardSupplyType": "term_limited"},
        ],
    )


def test_build_card_list_payload_from_masterdata(tmp_path):
    _write_minimal_master(tmp_path)

    payload = build_payload(tmp_path, card_ids=[101, 102], region="jp", title="Cards")

    assert payload["region"] == "jp"
    assert payload["title"] == "Cards"
    assert payload["term_limited_icon_path"] == "static_images/card/term_limited.png"
    assert payload["fes_limited_icon_path"] == "static_images/card/fes_limited.png"

    first = payload["cards"][0]
    assert first["card_id"] == 101
    assert first["character_name"] == "星乃一歌"
    assert first["unit"] == "light_sound"
    assert first["supply_type"] == "期间限定"
    assert first["skill"]["skill_type_icon_path"] == "static_images/skill_score_up.png"
    assert first["skill"]["skill_detail"] == "5.0秒間 スコアが40%UPする"
    assert first["special_skill_info"]["skill_type_icon_path"] == "static_images/skill_life_recovery.png"
    assert first["power"] == {"power1": 120, "power2": 230, "power3": 340, "power_total": 690}
    assert [thumbnail["card_thumbnail_path"] for thumbnail in first["thumbnail_info"]] == [
        "asset/jp-assets/startapp/thumbnail/chara/res001_no101_normal.png",
        "asset/jp-assets/startapp/thumbnail/chara/res001_no101_after_training.png",
    ]
    assert first["thumbnail_info"][1]["rare_img_path"] == "static_images/card/rare_star_after_training.png"

    second = payload["cards"][1]
    assert second["unit"] == "light_sound"
    assert second["supply_type"] == "生日"
    assert second["thumbnail_info"] == [
        {
            "card_id": 102,
            "card_thumbnail_path": "asset/jp-assets/startapp/thumbnail/chara/res027_no102_normal.png",
            "rare": "rarity_birthday",
            "frame_img_path": "static_images/card/frame_rarity_birthday.png",
            "attr_img_path": "static_images/card/attr_happy.png",
            "rare_img_path": "static_images/card/rare_birthday.png",
            "train_rank": 0,
            "is_after_training": False,
            "is_pcard": False,
            "birthday_icon_path": "static_images/card/rare_birthday.png",
        }
    ]


def test_build_card_list_payload_errors_for_missing_card(tmp_path):
    _write_minimal_master(tmp_path)

    with pytest.raises(ValueError, match=r"999"):
        build_payload(tmp_path, card_ids=[999], region="jp", title=None)


def test_build_card_list_payload_maps_world_link3_supply(tmp_path):
    _write_minimal_master(tmp_path)
    _write_master(
        tmp_path,
        "events.json",
        [{"id": 5001, "eventType": "world_bloom", "unit": "none"}],
    )
    _write_master(
        tmp_path,
        "eventCards.json",
        [{"id": 1, "cardId": 101, "eventId": 5001}],
    )

    payload = build_payload(tmp_path, card_ids=[101], region="jp", title=None)

    assert payload["cards"][0]["supply_type"] == "WL限定"
