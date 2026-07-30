from __future__ import annotations

import pytest

from src.sekai.honor.bonds_plan import FullResizeClipOp, build_bonds_honor_plan


def _op(plan, name: str) -> FullResizeClipOp:
    return next(op for op in (*plan.masked_ops, *plan.post_mask_ops) if op.name == name)


def test_main_bonds_honor_plan_pins_full_resize_then_destination_clip_geometry():
    plan = build_bonds_honor_plan(
        left_background_size=(380, 80),
        right_background_size=(380, 80),
        chara_icon_1_size=(160, 136),
        chara_icon_2_size=(160, 136),
        is_main_honor=True,
        honor_rarity="low",
        honor_level=8,
        mask_size=(380, 80),
        frame_size=(364, 80),
        word_size=(380, 80),
        level_icon_size=(14, 14),
        level6_icon_size=(14, 14),
    )

    assert plan.badge_size == (380, 80)
    assert plan.mask is not None
    assert plan.mask.full_resize_size == (380, 80)
    assert plan.bare_background_fallback is False

    left_bg = _op(plan, "background.left")
    assert left_bg.full_resize_size == (380, 80)
    assert left_bg.destination_clip == (0, 0, 193, 80)
    assert left_bg.post_resize_crop_box == (0, 0, 193, 80)

    left = _op(plan, "character.left")
    right = _op(plan, "character.right")
    assert left.full_resize_size == right.full_resize_size == (128, 108)
    assert left.destination_offset == (6, -28)
    assert right.destination_offset == (246, -28)
    assert left.destination_clip == (0, 0, 190, 80)
    assert right.destination_clip == (190, 0, 380, 80)
    assert left.post_resize_crop_box == (0, 28, 128, 108)
    assert right.post_resize_crop_box == (0, 28, 128, 108)
    assert left.sampling == right.sampling == "linear"
    assert left.blend == right.blend == "src_over"

    frame = _op(plan, "frame")
    word = _op(plan, "word")
    assert frame.destination_offset == (8, 0)
    assert frame.blend == "paste_lerp"
    assert word.destination_offset == (0, 0)
    assert [op.destination_offset for op in plan.post_mask_ops if op.name.startswith("level.base.")] == [
        (50, 61),
        (66, 61),
        (82, 61),
        (98, 61),
        (114, 61),
    ]
    assert [op.destination_offset for op in plan.post_mask_ops if op.name.startswith("level.upgraded.")] == [
        (50, 61),
        (66, 61),
        (82, 61),
    ]
    assert plan.execution_order == (
        "mask.begin",
        "background.right",
        "background.left",
        "character.left",
        "character.right",
        "mask.end",
        "frame",
        "word",
        "level.base.0",
        "level.base.1",
        "level.base.2",
        "level.base.3",
        "level.base.4",
        "level.upgraded.0",
        "level.upgraded.1",
        "level.upgraded.2",
    )


def test_sub_bonds_honor_plan_clips_resized_characters_at_destination_midline():
    plan = build_bonds_honor_plan(
        left_background_size=(380, 80),
        right_background_size=(180, 80),
        chara_icon_1_size=(160, 136),
        chara_icon_2_size=(160, 136),
        is_main_honor=False,
        honor_rarity="middle",
        honor_level=0,
    )

    left_bg = _op(plan, "background.left")
    assert left_bg.source_size == (380, 80)
    assert left_bg.full_resize_size == (180, 80)
    assert left_bg.destination_clip == (0, 0, 93, 80)
    assert left_bg.post_resize_crop_box == (0, 0, 93, 80)

    left = _op(plan, "character.left")
    right = _op(plan, "character.right")
    assert left.destination_offset == (-4, -28)
    assert right.destination_offset == (56, -28)
    # These are crop boxes in the 128x108 *resized* sprites.  Treating them as source_rect
    # would crop the 160x136 source first and is deliberately a different operation.
    assert left.post_resize_crop_box == (4, 28, 94, 108)
    assert right.post_resize_crop_box == (34, 28, 124, 108)
    assert left.visible_destination_rect == (0, 0, 90, 80)
    assert right.visible_destination_rect == (90, 0, 180, 80)


def test_bonds_honor_plan_preserves_missing_character_early_return():
    plan = build_bonds_honor_plan(
        left_background_size=(180, 80),
        right_background_size=(180, 80),
        chara_icon_1_size=(160, 136),
        chara_icon_2_size=None,
        is_main_honor=False,
        honor_rarity="low",
        honor_level=8,
        mask_size=(180, 80),
        frame_size=(172, 80),
        level_icon_size=(14, 14),
        level6_icon_size=(14, 14),
    )

    assert plan.bare_background_fallback is True
    assert plan.mask is None
    assert plan.post_mask_ops == ()
    assert plan.execution_order == ("background.right", "background.left")


def test_bonds_honor_plan_keeps_legacy_level_wrap_and_overlay_order():
    plan = build_bonds_honor_plan(
        left_background_size=(380, 80),
        right_background_size=(380, 80),
        chara_icon_1_size=(160, 136),
        chara_icon_2_size=(160, 136),
        is_main_honor=True,
        honor_rarity="highest",
        honor_level=13,
        frame_size=(380, 80),
        word_size=(100, 20),
        level_icon_size=(14, 14),
        level6_icon_size=(14, 14),
    )

    assert _op(plan, "frame").destination_offset == (0, 0)
    assert _op(plan, "word").destination_offset == (140, 30)
    assert [op.name for op in plan.post_mask_ops] == [
        "frame",
        "word",
        "level.base.0",
        "level.base.1",
        "level.base.2",
    ]


def test_full_resize_clip_op_reports_empty_intersection_without_reinterpreting_source():
    op = FullResizeClipOp(
        name="outside",
        source_key="image",
        source_size=(20, 20),
        full_resize_size=(10, 10),
        destination_offset=(30, 30),
        destination_clip=(0, 0, 20, 20),
        sampling="linear",
        blend="src_over",
    )

    assert op.visible_destination_rect is None
    assert op.post_resize_crop_box is None


@pytest.mark.parametrize("size", [(0, 80), (180, 0), (-1, 80)])
def test_bonds_honor_plan_rejects_non_positive_source_dimensions(size):
    with pytest.raises(ValueError, match="positive dimensions"):
        build_bonds_honor_plan(
            left_background_size=size,
            right_background_size=(180, 80),
            chara_icon_1_size=None,
            chara_icon_2_size=None,
            is_main_honor=False,
            honor_rarity="low",
            honor_level=0,
        )
