"""Pixel-free geometry plan for one bonds honor badge.

The legacy compositor has one operation that is easy to mistranslate into Render IR:
the backgrounds and character sprites are resized as *whole images* and only then clipped
in destination space.  ``Image.source_rect`` has the opposite order (crop source, then
resize), so it cannot represent this plan.

``FullResizeClipOp`` is the minimal backend-neutral contract needed by both the standalone
honor route and custom-profile bonds honor slots:

1. decode the full source;
2. resize the full source to ``full_resize_size`` with ``sampling``;
3. place its top-left at ``destination_offset``;
4. clip the already-resized destination to ``destination_clip``;
5. apply ``tint`` and ``blend`` while compositing.

The data types in this module deliberately contain no Pillow images and perform no asset
resolution.  A Painter implementation can lower an op to a rectangular clip plus an ordinary
paste of the whole source at ``destination_offset``.  IR can lower it to a clipped Group plus
an Image node with no ``source_rect``.  Keeping the order explicit prevents a later native
adapter from accidentally restoring the old crop-before-resize parity bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

BondsSize: TypeAlias = tuple[int, int]
BondsPoint: TypeAlias = tuple[int, int]
BondsRect: TypeAlias = tuple[int, int, int, int]
BondsSampling: TypeAlias = Literal["nearest", "linear", "catmull_rom", "pillow_lanczos"]
BondsBlend: TypeAlias = Literal["src", "paste_lerp", "src_over"]
BondsTintMode: TypeAlias = Literal["multiply", "recolor"]

BONDS_BACKGROUND_CENTER_OVERLAP = 3
BONDS_CHARACTER_SCALE = 0.8
BONDS_MAIN_CHARACTER_OFFSET = 120
BONDS_SUB_CHARACTER_OFFSET = 30


@dataclass(frozen=True, slots=True)
class BondsImageTint:
    color: tuple[int, int, int, int]
    mode: BondsTintMode = "multiply"


@dataclass(frozen=True, slots=True)
class FullResizeClipOp:
    """One full-source resize followed by a destination-space clip.

    ``destination_clip`` uses badge coordinates and right/bottom-exclusive bounds.  It is
    intentionally not a source crop.  ``paste_lerp`` names Pillow's
    ``destination.paste(source, xy, source)`` arithmetic and must not be silently mapped to
    Porter-Duff SrcOver when exact alpha behavior matters.
    """

    name: str
    source_key: str
    source_size: BondsSize
    full_resize_size: BondsSize
    destination_offset: BondsPoint
    destination_clip: BondsRect
    sampling: BondsSampling
    blend: BondsBlend
    tint: BondsImageTint | None = None

    @property
    def destination_rect(self) -> BondsRect:
        x, y = self.destination_offset
        w, h = self.full_resize_size
        return x, y, x + w, y + h

    @property
    def visible_destination_rect(self) -> BondsRect | None:
        left, top, right, bottom = self.destination_rect
        clip_left, clip_top, clip_right, clip_bottom = self.destination_clip
        visible = (
            max(left, clip_left),
            max(top, clip_top),
            min(right, clip_right),
            min(bottom, clip_bottom),
        )
        if visible[0] >= visible[2] or visible[1] >= visible[3]:
            return None
        return visible

    @property
    def post_resize_crop_box(self) -> BondsRect | None:
        """Visible crop in the *resized image's* pixel coordinates.

        This is useful to compare a native implementation against the historical
        ``resize(...).crop(...)`` pipeline.  It must never be passed to an Image node as its
        source-pixel ``source_rect``.
        """

        visible = self.visible_destination_rect
        if visible is None:
            return None
        x, y = self.destination_offset
        return visible[0] - x, visible[1] - y, visible[2] - x, visible[3] - y


@dataclass(frozen=True, slots=True)
class BondsMaskPlan:
    source_key: str
    source_size: BondsSize
    full_resize_size: BondsSize
    destination_offset: BondsPoint
    sampling: BondsSampling = "catmull_rom"


@dataclass(frozen=True, slots=True)
class BondsHonorPlan:
    """Ordered, pixel-free composition plan for one natural-size badge."""

    badge_size: BondsSize
    mask: BondsMaskPlan | None
    masked_ops: tuple[FullResizeClipOp, ...]
    post_mask_ops: tuple[FullResizeClipOp, ...]
    bare_background_fallback: bool

    @property
    def execution_order(self) -> tuple[str, ...]:
        names = [op.name for op in self.masked_ops]
        if self.mask is not None:
            names = ["mask.begin", *names, "mask.end"]
        names.extend(op.name for op in self.post_mask_ops)
        return tuple(names)


def _checked_size(name: str, size: BondsSize) -> BondsSize:
    width, height = int(size[0]), int(size[1])
    if width <= 0 or height <= 0:
        raise ValueError(f"{name} must have positive dimensions")
    return width, height


def _image_op(
    *,
    name: str,
    source_key: str,
    source_size: BondsSize,
    destination_offset: BondsPoint,
    badge_size: BondsSize,
    full_resize_size: BondsSize | None = None,
    destination_clip: BondsRect | None = None,
    sampling: BondsSampling = "catmull_rom",
    blend: BondsBlend = "paste_lerp",
) -> FullResizeClipOp:
    source_size = _checked_size(source_key, source_size)
    resize_size = source_size if full_resize_size is None else _checked_size(f"{source_key} resize", full_resize_size)
    width, height = badge_size
    return FullResizeClipOp(
        name=name,
        source_key=source_key,
        source_size=source_size,
        full_resize_size=resize_size,
        destination_offset=(int(destination_offset[0]), int(destination_offset[1])),
        destination_clip=destination_clip or (0, 0, width, height),
        sampling=sampling,
        blend=blend,
    )


def _build_post_mask_ops(
    *,
    badge_size: BondsSize,
    is_main_honor: bool,
    honor_rarity: str,
    honor_level: int,
    frame_size: BondsSize | None,
    word_size: BondsSize | None,
    level_icon_size: BondsSize | None,
    level6_icon_size: BondsSize | None,
) -> tuple[FullResizeClipOp, ...]:
    ops: list[FullResizeClipOp] = []
    if frame_size is not None:
        ops.append(
            _image_op(
                name="frame",
                source_key="frame_img",
                source_size=frame_size,
                destination_offset=(8, 0) if honor_rarity == "low" else (0, 0),
                badge_size=badge_size,
            )
        )
    if is_main_honor and word_size is not None:
        word_width, word_height = _checked_size("word_img", word_size)
        ops.append(
            _image_op(
                name="word",
                source_key="word_img",
                source_size=(word_width, word_height),
                destination_offset=(int(190 - word_width / 2), int(40 - word_height / 2)),
                badge_size=badge_size,
            )
        )

    visible_level = int(honor_level)
    if visible_level > 10:
        visible_level -= 10
    if level_icon_size is not None:
        for index in range(min(max(0, visible_level), 5)):
            ops.append(
                _image_op(
                    name=f"level.base.{index}",
                    source_key="lv_img",
                    source_size=level_icon_size,
                    destination_offset=(50 + 16 * index, 61),
                    badge_size=badge_size,
                )
            )
    if level6_icon_size is not None:
        for index in range(5, max(0, visible_level)):
            ops.append(
                _image_op(
                    name=f"level.upgraded.{index - 5}",
                    source_key="lv6_img",
                    source_size=level6_icon_size,
                    destination_offset=(50 + 16 * (index - 5), 61),
                    badge_size=badge_size,
                )
            )
    return tuple(ops)


def build_bonds_honor_plan(
    *,
    left_background_size: BondsSize,
    right_background_size: BondsSize,
    chara_icon_1_size: BondsSize | None,
    chara_icon_2_size: BondsSize | None,
    is_main_honor: bool,
    honor_rarity: str,
    honor_level: int,
    mask_size: BondsSize | None = None,
    frame_size: BondsSize | None = None,
    word_size: BondsSize | None = None,
    level_icon_size: BondsSize | None = None,
    level6_icon_size: BondsSize | None = None,
) -> BondsHonorPlan:
    """Build the exact geometry and draw order used by ``HonorBadgeBox._draw_bonds``.

    The right background defines the badge canvas.  If either character is missing, the
    historical early return is preserved: only the two background halves are drawn, with no
    mask, frame, word, or level stars.
    """

    badge_size = _checked_size("right background", right_background_size)
    left_background_size = _checked_size("left background", left_background_size)
    width, height = badge_size
    canvas_clip = (0, 0, width, height)
    left_width = min(width, width // 2 + BONDS_BACKGROUND_CENTER_OVERLAP)

    background_ops = (
        _image_op(
            name="background.right",
            source_key="bonds_bg2",
            source_size=badge_size,
            destination_offset=(0, 0),
            badge_size=badge_size,
            full_resize_size=badge_size,
            destination_clip=canvas_clip,
            sampling="linear",
            blend="src",
        ),
        _image_op(
            name="background.left",
            source_key="bonds_bg",
            source_size=left_background_size,
            destination_offset=(0, 0),
            badge_size=badge_size,
            full_resize_size=badge_size,
            destination_clip=(0, 0, left_width, height),
            sampling="linear",
            blend="paste_lerp",
        ),
    )

    if chara_icon_1_size is None or chara_icon_2_size is None:
        return BondsHonorPlan(
            badge_size=badge_size,
            mask=None,
            masked_ops=background_ops,
            post_mask_ops=(),
            bare_background_fallback=True,
        )

    c1_source_size = _checked_size("chara_icon_1", chara_icon_1_size)
    c2_source_size = _checked_size("chara_icon_2", chara_icon_2_size)
    c1_resize = (
        int(c1_source_size[0] * BONDS_CHARACTER_SCALE),
        int(c1_source_size[1] * BONDS_CHARACTER_SCALE),
    )
    c2_resize = (
        int(c2_source_size[0] * BONDS_CHARACTER_SCALE),
        int(c2_source_size[1] * BONDS_CHARACTER_SCALE),
    )
    c1_face = int((c1_source_size[0] // 2) * BONDS_CHARACTER_SCALE)
    c2_face = int((c2_source_size[0] // 2) * BONDS_CHARACTER_SCALE)
    middle = width // 2
    offset = BONDS_MAIN_CHARACTER_OFFSET if is_main_honor else BONDS_SUB_CHARACTER_OFFSET
    c1_face_x = middle - offset
    c2_face_x = middle + offset
    c1_origin = (c1_face_x - c1_face, height - c1_resize[1])
    c2_origin = (c2_face_x - c2_face, height - c2_resize[1])

    character_ops = (
        _image_op(
            name="character.left",
            source_key="chara_icon_1",
            source_size=c1_source_size,
            destination_offset=c1_origin,
            badge_size=badge_size,
            full_resize_size=c1_resize,
            destination_clip=(0, 0, middle, height),
            sampling="linear",
            blend="src_over",
        ),
        _image_op(
            name="character.right",
            source_key="chara_icon_2",
            source_size=c2_source_size,
            destination_offset=c2_origin,
            badge_size=badge_size,
            full_resize_size=c2_resize,
            destination_clip=(middle, 0, width, height),
            sampling="linear",
            blend="src_over",
        ),
    )

    post_mask_ops = _build_post_mask_ops(
        badge_size=badge_size,
        is_main_honor=is_main_honor,
        honor_rarity=honor_rarity,
        honor_level=honor_level,
        frame_size=frame_size,
        word_size=word_size,
        level_icon_size=level_icon_size,
        level6_icon_size=level6_icon_size,
    )

    mask = None
    if mask_size is not None:
        mask = BondsMaskPlan(
            source_key="mask_img",
            source_size=_checked_size("mask_img", mask_size),
            full_resize_size=badge_size,
            destination_offset=(0, 0),
        )
    return BondsHonorPlan(
        badge_size=badge_size,
        mask=mask,
        masked_ops=(*background_ops, *character_ops),
        post_mask_ops=post_mask_ops,
        bare_background_fallback=False,
    )


__all__ = [
    "BONDS_BACKGROUND_CENTER_OVERLAP",
    "BONDS_CHARACTER_SCALE",
    "BONDS_MAIN_CHARACTER_OFFSET",
    "BONDS_SUB_CHARACTER_OFFSET",
    "BondsHonorPlan",
    "BondsImageTint",
    "BondsMaskPlan",
    "FullResizeClipOp",
    "build_bonds_honor_plan",
]
