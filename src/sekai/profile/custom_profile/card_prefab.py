"""Renderer-neutral display lists for custom-profile card prefabs.

The legacy Pillow renderer and the native emitter must consume the same operation sequence.
In particular, Deck/clip art is intentionally a two-stage operation (cover-resize, then crop),
and the General ``Deck`` widget performs a third Lanczos resize after all overlays are composed.
Collapsing those stages changes pixels even when the final geometry looks identical.

No active card path adds a mask: Leader/full and Deck/clip preserve their historical unmasked
output. ``CardAlphaMaskOp`` only represents the pre-existing opt-in hook on
``compose_deck_card_view`` so replaying that internal API does not silently change behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from src.sekai.profile.custom_profile.general_prefab import rect_transform_box

AssetPath: TypeAlias = str | Path
Blend = Literal["src_over", "src"]
Color = tuple[int, int, int, int]
Rect = tuple[float, float, float, float]
ResourcePolicy = Literal["required", "optional"]
Sampling = Literal["nearest", "bilinear", "bicubic", "lanczos"]


@dataclass(frozen=True, slots=True)
class CardFontRef:
    """Logical card font plus the resolved path a future native emitter can consume."""

    path: AssetPath | None = None
    name: str = "general"
    bold: bool = True


@dataclass(frozen=True, slots=True)
class CardSpriteRef:
    """Unity sprite identity with resolved and compatibility-fallback asset paths."""

    name: str
    path: AssetPath | None = None
    fallback_path: AssetPath | None = None
    resource_policy: ResourcePolicy = "optional"


@dataclass(frozen=True, slots=True)
class CardPrefabResources:
    art_path: AssetPath | None
    frame: CardSpriteRef
    attribute: CardSpriteRef
    rarity: CardSpriteRef
    master_rank: CardSpriteRef | None = None
    leader_label: CardSpriteRef | None = None


@dataclass(frozen=True, slots=True)
class CardCoverArtOp:
    """Cover-resize an art asset, then crop the result into the display-list canvas."""

    path: AssetPath
    cover_size: tuple[float, float]
    cover_align: tuple[float, float] = (0.5, 0.5)
    crop_align: tuple[float, float] = (0.5, 0.5)
    sampling: Sampling = "lanczos"
    blend: Blend = "src_over"

    def __post_init__(self) -> None:
        if self.blend not in {"src_over", "src"}:
            raise ValueError(f"unsupported card art blend: {self.blend!r}")


@dataclass(frozen=True, slots=True)
class CardRectOp:
    rect: Rect
    fill: Color | None
    blend: Blend = "src"
    round_coordinates: bool = True
    radius: float = 0.0
    outline: Color | None = None
    width: int = 1

    def __post_init__(self) -> None:
        if self.blend not in {"src_over", "src"}:
            raise ValueError(f"unsupported card rect blend: {self.blend!r}")


@dataclass(frozen=True, slots=True)
class CardTextOp:
    text: str
    pos: tuple[float, float]
    size: int
    fill: Color
    anchor: str | None = None
    font: CardFontRef = CardFontRef()


@dataclass(frozen=True, slots=True)
class CardSpriteOp:
    resource: CardSpriteRef
    rect: Rect
    sampling: Sampling = "lanczos"


@dataclass(frozen=True, slots=True)
class CardAlphaMaskOp:
    """Multiply current alpha by a Unity mask, with the historical rounded fallback."""

    resource: CardSpriteRef
    fallback_radius_ratio: float = 0.03
    sampling: Sampling = "lanczos"


CardOp: TypeAlias = CardCoverArtOp | CardRectOp | CardTextOp | CardSpriteOp | CardAlphaMaskOp


@dataclass(frozen=True, slots=True)
class CardDisplayList:
    kind: Literal["full", "deck"]
    size: tuple[int, int]
    ops: tuple[CardOp, ...]
    render_size: tuple[int, int] | None = None
    final_sampling: Sampling = "lanczos"

    def __post_init__(self) -> None:
        if min(self.size) <= 0:
            raise ValueError("a card display list needs positive native dimensions")
        if self.render_size is not None and min(self.render_size) <= 0:
            raise ValueError("a card display list needs positive render dimensions")


def build_card_rarity_ops(
    resource: CardSpriteRef,
    positions: tuple[Rect, ...] | list[Rect],
    count: int,
) -> tuple[CardSpriteOp, ...]:
    return tuple(CardSpriteOp(resource, rect) for rect in positions[: max(0, min(4, int(count)))])


def build_deck_card_level_ops(
    size: tuple[int, int],
    level: int,
    *,
    font: CardFontRef = CardFontRef(),
) -> tuple[CardOp, ...]:
    lv_rect = rect_transform_box(
        size,
        (0.0, 0.0),
        (1.0, 0.0),
        (0.0, 0.0),
        (0.0, 56.38999938964844),
        (0.5, 0.0),
    )
    text_rect = rect_transform_box(
        (lv_rect[2] - lv_rect[0], lv_rect[3] - lv_rect[1]),
        (0.0, 0.0),
        (0.0, 1.0),
        (12.9, 0.7),
        (117.76000213623047, -9.569999694824219),
        (0.0, 0.5),
    )
    text_rect = (
        text_rect[0] + lv_rect[0],
        text_rect[1] + lv_rect[1],
        text_rect[2] + lv_rect[0],
        text_rect[3] + lv_rect[1],
    )
    return (
        CardRectOp(lv_rect, (38, 39, 62, 230), blend="src"),
        CardTextOp(
            f"Lv.{max(1, int(level))}",
            (text_rect[0], (text_rect[1] + text_rect[3]) / 2.0),
            28,
            (255, 255, 255, 255),
            "lm",
            font,
        ),
    )


def build_deck_leader_label_op(
    size: tuple[int, int],
    resource: CardSpriteRef,
) -> CardSpriteOp:
    return CardSpriteOp(
        resource,
        rect_transform_box(
            size,
            (1.0, 1.0),
            (1.0, 1.0),
            (0.0, 0.0),
            (164.0, 94.0),
            (1.0, 1.0),
        ),
    )


def build_deck_card_overlay_ops(
    size: tuple[int, int],
    resources: CardPrefabResources,
    rarity_count: int,
    *,
    attr_x: float,
    leader: bool,
) -> tuple[CardSpriteOp, ...]:
    ops: list[CardSpriteOp] = [
        CardSpriteOp(resources.frame, (0.0, 0.0, float(size[0]), float(size[1]))),
        CardSpriteOp(
            resources.attribute,
            rect_transform_box(
                size,
                (0.0, 1.0),
                (0.0, 1.0),
                (attr_x, 0.0),
                (64.0, 68.0),
                (0.0, 1.0),
            ),
        ),
    ]
    star_size = 56.0 * 0.8
    star_positions = tuple(
        (
            5.0 + index * 40.0,
            size[1] - 64.0 - star_size,
            5.0 + index * 40.0 + star_size,
            size[1] - 64.0,
        )
        for index in range(4)
    )
    ops.extend(build_card_rarity_ops(resources.rarity, star_positions, rarity_count))
    if resources.master_rank is not None:
        ops.append(
            CardSpriteOp(
                resources.master_rank,
                rect_transform_box(
                    size,
                    (1.0, 0.0),
                    (1.0, 0.0),
                    (1.4, 0.8),
                    (88.0 * 0.95, 88.0 * 0.95),
                    (1.0, 0.0),
                ),
            )
        )
    if leader and resources.leader_label is not None:
        ops.append(build_deck_leader_label_op(size, resources.leader_label))
    return tuple(ops)


def build_full_card_overlay_ops(
    size: tuple[int, int],
    resources: CardPrefabResources,
    rarity_count: int,
) -> tuple[CardSpriteOp, ...]:
    ops: list[CardSpriteOp] = [
        CardSpriteOp(resources.frame, (0.0, 0.0, float(size[0]), float(size[1]))),
        CardSpriteOp(
            resources.attribute,
            rect_transform_box(
                size,
                (1.0, 1.0),
                (1.0, 1.0),
                (-40.0, 0.0),
                (88.0, 92.0),
                (1.0, 1.0),
            ),
        ),
    ]
    star_positions = (
        (
            24.2 + 0.37,
            size[1] - (17.0 + 10.75998592376709 + 55.7599983215332),
            24.2 + 0.37 + 55.7599983215332,
            size[1] - (17.0 + 10.75998592376709),
        ),
        (
            24.2 + 0.37,
            size[1] - (17.0 + 58.81999969482422 + 55.7599983215332),
            24.2 + 0.37 + 55.7599983215332,
            size[1] - (17.0 + 58.81999969482422),
        ),
        (
            24.2 + 0.37,
            size[1] - (17.0 + 106.88999938964844 + 55.7599983215332),
            24.2 + 0.37 + 55.7599983215332,
            size[1] - (17.0 + 106.88999938964844),
        ),
        (
            24.2 + 0.37,
            size[1] - (17.0 + 154.9600067138672 + 55.7599983215332),
            24.2 + 0.37 + 55.7599983215332,
            size[1] - (17.0 + 154.9600067138672),
        ),
    )
    ops.extend(build_card_rarity_ops(resources.rarity, star_positions, rarity_count))
    if resources.master_rank is not None:
        ops.append(
            CardSpriteOp(
                resources.master_rank,
                rect_transform_box(
                    size,
                    (1.0, 0.0),
                    (1.0, 0.0),
                    (-24.0, 24.0),
                    (104.0, 104.0),
                    (1.0, 0.0),
                ),
            )
        )
    return tuple(ops)


def build_full_card_display_list(
    *,
    size: tuple[int, int],
    resources: CardPrefabResources,
    rarity_count: int,
    show_detail: bool,
) -> CardDisplayList:
    if resources.art_path is None:
        raise ValueError("a full card display list needs a card art path")
    ops: list[CardOp] = [
        CardCoverArtOp(
            resources.art_path,
            (float(size[0]), float(size[1])),
            blend="src",
        )
    ]
    if show_detail:
        ops.extend(build_full_card_overlay_ops(size, resources, rarity_count))
    return CardDisplayList("full", size, tuple(ops))


def build_deck_card_display_list(
    *,
    native_size: tuple[int, int],
    art_size: tuple[float, float],
    crop_align_y: float,
    resources: CardPrefabResources,
    rarity_count: int,
    level: int,
    leader: bool,
    show_detail: bool,
    attr_x: float,
    mask: CardSpriteRef | None = None,
    render_size: tuple[int, int] | None = None,
    font: CardFontRef = CardFontRef(),
) -> CardDisplayList:
    if resources.art_path is None:
        raise ValueError("a deck card display list needs a card art path")
    ops: list[CardOp] = [
        CardCoverArtOp(
            resources.art_path,
            art_size,
            crop_align=(0.5, crop_align_y),
            blend="src_over",
        )
    ]
    if show_detail:
        ops.extend(build_deck_card_level_ops(native_size, level, font=font))
    if mask is not None:
        ops.append(CardAlphaMaskOp(mask))
    if show_detail:
        ops.extend(
            build_deck_card_overlay_ops(
                native_size,
                resources,
                rarity_count,
                attr_x=attr_x,
                leader=leader,
            )
        )
    return CardDisplayList("deck", native_size, tuple(ops), render_size)


def build_empty_deck_card_display_list(size: tuple[int, int]) -> CardDisplayList:
    """Build the historical rounded placeholder used for a missing General Deck member."""

    rect = (0.0, 0.0, float(size[0] - 1), float(size[1] - 1))
    return CardDisplayList(
        "deck",
        size,
        (
            CardRectOp(rect, (226, 232, 240, 255), radius=8),
            CardRectOp(
                rect,
                None,
                radius=8,
                outline=(170, 183, 198, 255),
                width=2,
            ),
        ),
    )


def __getattr__(name):
    # Keep legacy imports working without loading Pillow for display-list consumers.
    if name in (
        "PillowAssetLoader",
        "PillowCardAdapter",
        "PillowFontFactory",
        "PillowSpriteLoader",
        "PillowSpritePaster",
    ):
        from importlib import import_module

        return getattr(import_module(".pillow_card_prefab", __package__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
