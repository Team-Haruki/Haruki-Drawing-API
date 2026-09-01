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
    def __init__(self, *, cards: dict[int, dict] | None = None, tables: dict[str, list[dict]] | None = None):
        self._cards = cards or {}
        self._tables = tables or {}

    def card_by_id(self) -> dict[int, dict]:
        return self._cards

    def character_by_id(self) -> dict[int, dict]:
        return {}

    def get(self, table: str) -> list[dict]:
        return self._tables.get(table, [])


def test_gacha_pickups_and_guarantee_preserve_priority() -> None:
    gacha = {
        "gachaPickups": [{"cardId": 2}, {"cardId": 1}, {"cardId": 2}],
        "gachaBehaviors": [
            {"gachaBehaviorType": "over_rarity_4_once"},
            {"gachaBehaviorType": "over_rarity_3_once"},
        ],
    }

    assert payloads._gacha_pickup_order(gacha) == [2, 1]
    assert payloads._gacha_guaranteed_type(gacha) == "rarity_4"


def test_gacha_weight_helpers_calculate_pickup_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    cards = {
        1: {"id": 1, "cardRarityType": "rarity_4"},
        2: {"id": 2, "cardRarityType": "rarity_4"},
    }
    monkeypatch.setattr(payloads, "MD", _FakeMasterData(cards=cards))
    gacha = {
        "gachaDetails": [
            {"cardId": 1, "weight": 3},
            {"cardId": 2, "weight": 1},
            {"cardId": 999, "weight": 100},
        ],
        "gachaCardRarityRates": [
            {"lotteryType": "normal", "cardRarityType": "rarity_4", "rate": 3.0},
            {"lotteryType": "guaranteed", "cardRarityType": "rarity_4", "rate": 100.0},
        ],
    }

    counts, card_weight, card_rarity, rarity_weights = payloads._gacha_card_weights(gacha)
    fractions, weight_info = payloads._gacha_normal_rates(gacha, counts)

    assert counts["rarity_4"] == 2
    assert weight_info["rarity_4_rate"] == pytest.approx(0.03)
    assert payloads._gacha_card_rate(1, card_rarity, rarity_weights, fractions, card_weight) == pytest.approx(0.0225)
    assert payloads._gacha_card_rate(999, card_rarity, rarity_weights, fractions, card_weight) == 0.0


def test_gacha_guaranteed_rates_move_lower_rarities() -> None:
    fractions = {"rarity_2": 0.85, "rarity_3": 0.12, "rarity_4": 0.03}

    rarity_three = payloads._gacha_guaranteed_rates(fractions, "rarity_3")
    rarity_four = payloads._gacha_guaranteed_rates(fractions, "rarity_4")

    assert rarity_three["rarity_2"] == 0.0
    assert rarity_three["rarity_3"] == pytest.approx(0.97)
    assert rarity_four["rarity_2"] == 0.0
    assert rarity_four["rarity_3"] == 0.0
    assert rarity_four["rarity_4"] == pytest.approx(1.0)


def test_costume_optional_fields_and_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(payloads, "MD", _FakeMasterData())
    monkeypatch.setattr(payloads, "_costume_thumbnail_path", lambda costume: f"thumb/{costume['id']}")
    costume = {
        "id": 10,
        "costume3dGroupId": 1,
        "name": "Main",
        "partType": "body",
        "costume3dType": "normal",
        "characterId": 21,
        "assetbundleName": "cos_main",
        "colorId": 1,
        "publishedAt": 100,
        "designer": "Designer",
    }
    variants = [
        costume,
        {**costume, "id": 11, "colorId": 2, "colorName": "Blue", "assetbundleName": "cos_blue"},
    ]

    result = payloads._costume_basic(costume, {10: [3], 11: [4, 5]}, variants)

    assert result["asset_bundle_name"] == "cos_main"
    assert result["published_at"] == 100
    assert result["designer"] == "Designer"
    assert result["source_card_ids"] == [3, 4, 5]
    assert result["variants"][1]["source_card_ids"] == [4, 5]


def test_paginate_by_part_balances_known_then_unknown_parts() -> None:
    items = [
        {"id": 1, "partType": "head"},
        {"id": 2, "partType": "body"},
        {"id": 3, "partType": "other"},
        {"id": 4, "partType": "head"},
        {"id": 5, "partType": "body"},
        {"id": 6, "partType": "other"},
    ]

    assert [item["id"] for item in payloads._paginate_by_part(items, page_size=4, page=1)] == [2, 1, 3, 5]
    assert [item["id"] for item in payloads._paginate_by_part(items, page_size=4, page=2)] == [4, 6]
    assert payloads._paginate_by_part(items, page_size=4, page=0) == []


def test_vlive_rewards_use_first_nonempty_normal_box(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        payloads,
        "_material_icon",
        lambda resource_type, resource_id: f"icon/{resource_id}" if resource_type == "material" else "",
    )
    live = {
        "virtualLiveRewards": [
            {"virtualLiveType": "archive", "resourceBoxId": 1},
            {"virtualLiveType": "normal", "resourceBoxId": 2},
            {"virtualLiveType": "", "resourceBoxId": 3},
        ]
    }
    boxes = {
        2: {"details": [{"resourceType": "unknown", "resourceId": 1, "resourceQuantity": 9}]},
        3: {"details": [{"resourceType": "material", "resourceId": 2, "resourceQuantity": 0}]},
    }

    assert payloads._vlive_rewards(live, boxes) == [{"image_path": "icon/2", "quantity": 1}]


def test_resolve_vlive_selects_current_window_and_rejects_out_of_range() -> None:
    base = 1_700_000_000_000
    now = base + 10_000
    live = {
        "id": 1,
        "startAt": base + 1_000,
        "endAt": base + 20_000,
        "virtualLiveSchedules": [
            {"startAt": base + 2_000, "endAt": base + 3_000},
            {"startAt": base + 9_000, "endAt": base + 11_000},
            {"startAt": base + 12_000, "endAt": base + 13_000},
        ],
    }

    resolved = payloads._resolve_vlive(live, now)

    assert resolved is not None
    assert resolved[3:] == ((base + 9_000, base + 11_000), True, 1)
    assert payloads._resolve_vlive({**live, "endAt": now}, now) is None
    assert payloads._resolve_vlive({**live, "startAt": 0}, now) is None


def test_vlive_brief_adds_only_resolved_optional_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    live = {"id": 7, "name": ""}
    resolved = (live, 1_000, 2_000, (1_100, 1_200), False, 2)
    monkeypatch.setattr(payloads, "_vlive_banner_path", lambda _live, _event: "banner.png")
    monkeypatch.setattr(payloads, "_vlive_rewards", lambda _live, _boxes: [{"quantity": 1}])
    monkeypatch.setattr(payloads, "_vlive_characters", lambda _live, _units: [{"icon_path": "icon.png"}])

    result = payloads._vlive_brief(resolved, {}, {}, {7: {"id": 1}})

    assert result["name"] == "Virtual Live #7"
    assert result["current_start_at"] == 1_100
    assert result["banner_path"] == "banner.png"
    assert result["rewards"] == [{"quantity": 1}]
    assert result["characters"] == [{"icon_path": "icon.png"}]
