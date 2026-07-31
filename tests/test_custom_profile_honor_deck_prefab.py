from __future__ import annotations

import pytest

from src.sekai.profile.custom_profile.honor_deck_prefab import (
    HONOR_DECK_MAIN_BADGE_SIZE,
    HONOR_DECK_MAX_SLOTS,
    HONOR_DECK_NATURAL_SIZE,
    HONOR_DECK_SERVICE_RASTER_SIZE,
    HONOR_DECK_SUB_BADGE_SIZE,
    build_honor_deck_plan,
    honor_deck_request_candidates,
)


def test_honor_deck_plan_separates_natural_and_service_raster_sizes():
    plan = build_honor_deck_plan([{"seq": 1, "honorId": 80, "honorLevel": 2}])

    assert plan is not None
    assert plan.natural_size == HONOR_DECK_NATURAL_SIZE == (783, 179)
    assert plan.service_raster_size == HONOR_DECK_SERVICE_RASTER_SIZE == (875, 200)


def test_honor_deck_plan_sorts_first_three_and_assigns_shared_slot_geometry():
    rows = [
        {"seq": 30, "honorId": 300, "honorLevel": 3},
        {"seq": 10, "honorId": 100, "honorLevel": 1},
        {"seq": 40, "honorId": 400, "honorLevel": 4},
        {"seq": 20, "honorId": 200, "honorLevel": 2},
    ]

    plan = build_honor_deck_plan(rows)

    assert plan is not None
    assert plan.expected_slot_count == HONOR_DECK_MAX_SLOTS == 3
    assert [slot.seq for slot in plan.slots] == [10, 20, 30]
    assert [slot.honor_id for slot in plan.slots] == [100, 200, 300]
    assert [slot.mode for slot in plan.slots] == ["main", "sub", "sub"]
    assert [slot.full_size for slot in plan.slots] == [True, False, False]
    assert [slot.natural_size for slot in plan.slots] == [
        HONOR_DECK_MAIN_BADGE_SIZE,
        HONOR_DECK_SUB_BADGE_SIZE,
        HONOR_DECK_SUB_BADGE_SIZE,
    ]
    assert [slot.target_rect for slot in plan.slots] == [
        (13.5, 49.5, 393.5, 129.5),
        (401.5, 49.5, 581.5, 129.5),
        (589.5, 49.5, 769.5, 129.5),
    ]
    assert [slot.target_xy for slot in plan.slots] == [(14, 50), (402, 50), (590, 50)]
    assert [slot.target_size for slot in plan.slots] == [(380, 80), (180, 80), (180, 80)]


def test_honor_deck_request_candidates_preserve_profile_then_ordinary_fallback():
    candidates = honor_deck_request_candidates(
        seq=7,
        honor_id=6833,
        honor_level=3,
        full_size=False,
    )

    assert candidates.profile_keys == (
        "profile:7",
        "profile:6833:7",
        "6833:3:sub",
        "6833",
    )
    assert candidates.ordinary_keys == ("6833:3:sub", "6833")
    assert candidates.ordered() == (
        ("profile", "profile:7"),
        ("profile", "profile:6833:7"),
        ("profile", "6833:3:sub"),
        ("profile", "6833"),
        ("ordinary", "6833:3:sub"),
        ("ordinary", "6833"),
    )
    assert tuple(candidates.iter_ordered()) == candidates.ordered()


def test_honor_deck_slot_carries_candidates_and_an_immutable_row_copy():
    row = {"seq": 1, "honorId": 80, "honorLevel": 2, "profileHonorType": "normal"}
    plan = build_honor_deck_plan([row])
    assert plan is not None

    row["honorId"] = 999
    slot = plan.slots[0]
    assert slot.honor_id == 80
    assert slot.profile_row["honorId"] == 80
    assert slot.request_candidates.profile_keys == (
        "profile:1",
        "profile:80:1",
        "80:2:main",
        "80",
    )
    assert slot.request_candidates.ordinary_keys == ("80:2:main", "80")
    with pytest.raises(TypeError):
        slot.profile_row["honorId"] = 999  # type: ignore[index]


def test_honor_deck_panel_is_optional_and_has_no_fallback():
    plan = build_honor_deck_plan([{"seq": 1}], include_panel=True)
    assert plan is not None
    assert plan.panel is not None
    assert plan.panel.sprite_name == "bg_base_r16_wh"
    assert plan.panel.target_rect == (0.0, 0.0, 783.0, 179.0)
    assert plan.panel.tint == (0.87451, 0.87451, 0.917647, 0.8)
    assert plan.panel.sliced_border == (21, 21, 21, 21)
    assert plan.panel.resource_policy == "optional"
    assert plan.panel.fallback is None

    without_panel = build_honor_deck_plan([{"seq": 1}], include_panel=False)
    assert without_panel is not None
    assert without_panel.panel is None


def test_honor_deck_plan_requires_at_least_one_profile_row():
    assert build_honor_deck_plan(None) is None
    assert build_honor_deck_plan([]) is None


def test_honor_deck_plan_rejects_non_mapping_rows():
    with pytest.raises(TypeError, match="must be mappings"):
        build_honor_deck_plan([{"seq": 1}, 2])  # type: ignore[list-item]
