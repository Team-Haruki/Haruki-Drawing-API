"""Shared display lists for the small custom-profile ``GeneralContentView`` prefabs.

The Unity prefab JSON remains the source of the constants below.  This module turns the
profile values into a renderer-neutral list of sprite and text operations.  Pillow currently
replays that list; the native custom-profile path can later replay the same list into Render
IR without growing a second copy of the layout.

Text fitting and wrapping depend on the selected font, so the pure builder accepts a metrics
protocol.  That boundary is intentional: Pillow supplies the compatibility implementation
today, while a native metrics provider can replace it without changing any prefab geometry.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias

from PIL import Image, ImageChops, ImageDraw, ImageFont

Color = tuple[int, int, int, int]
Tint = tuple[float, float, float, float] | Color
Rect = tuple[float, float, float, float]
Sampling = Literal["nearest", "bilinear", "bicubic", "lanczos"]
ImageFit = Literal["stretch", "cover"]
ResourcePolicy = Literal["required", "optional", "fallback"]
AssetPath = str | Path

CHARA_LIST: tuple[tuple[str | None, int | None], ...] = (
    ("miku", 21),
    ("rin", 22),
    ("len", 23),
    ("luka", 24),
    ("meiko", 25),
    ("kaito", 26),
    (None, None),
    (None, None),
    ("ick", 1),
    ("saki", 2),
    ("hnm", 3),
    ("shiho", 4),
    ("mnr", 5),
    ("hrk", 6),
    ("airi", 7),
    ("szk", 8),
    ("khn", 9),
    ("an", 10),
    ("akt", 11),
    ("toya", 12),
    ("tks", 13),
    ("emu", 14),
    ("nene", 15),
    ("rui", 16),
    ("knd", 17),
    ("mfy", 18),
    ("ena", 19),
    ("mzk", 20),
)
CHARACTER_RANK_CELL_SIZE = (196.0, 85.0)
CHARACTER_RANK_CELL_CENTER_X = (137.5, 348.5, 559.5, 770.5)
CHARACTER_RANK_ROW_STEP = 100.0
CHARACTER_RANK_NON_SCROLL_FIRST_CENTER_Y = 146.5
CHARACTER_RANK_SCROLL_VIEWPORT = (24.0, 104.0, 884.0, 524.0)
CHARACTER_RANK_SCROLL_CONTENT_SIZE = (860, 685)
CHARACTER_RANK_SCROLL_FIRST_CENTER_Y = 42.0


@dataclass(frozen=True, slots=True)
class GeneralFontRef:
    name: str = "FOT-RodinNTLGPro-DB"
    bold: bool = True


GENERAL_FONT = GeneralFontRef()


@dataclass(frozen=True, slots=True)
class GeneralPrefabPalette:
    input_tint: Tint
    dark_tint: Tint
    total_line_tint: Tint
    text: Color
    label_text: Color


@dataclass(frozen=True, slots=True)
class GeneralSpriteOp:
    name: str
    rect: Rect
    tint: Tint | None = None
    sliced_border: tuple[int, int, int, int] | None = None
    sampling: Sampling = "lanczos"
    resource_policy: ResourcePolicy = "optional"
    fallback: GeneralRoundedRectOp | None = None

    def __post_init__(self) -> None:
        if self.resource_policy == "fallback" and self.fallback is None:
            raise ValueError("a fallback sprite resource needs a fallback operation")
        if self.resource_policy != "fallback" and self.fallback is not None:
            raise ValueError("a sprite fallback is only valid with resource_policy='fallback'")


@dataclass(frozen=True, slots=True)
class GeneralRoundedRectOp:
    rect: Rect
    radius: float
    fill: Color | None
    outline: Color | None = None
    width: int = 1
    round_coordinates: bool = False


@dataclass(frozen=True, slots=True)
class GeneralAssetImageOp:
    resource_key: str
    path: AssetPath | None
    rect: Rect
    sampling: Sampling = "lanczos"
    fit: ImageFit = "stretch"
    align: tuple[float, float] = (0.5, 0.5)
    clip_radius: float | None = None
    resource_policy: ResourcePolicy = "optional"
    fallback: GeneralRoundedRectOp | None = None

    def __post_init__(self) -> None:
        if self.fit not in {"stretch", "cover"}:
            raise ValueError(f"unsupported GeneralContentView image fit: {self.fit}")
        if not all(0.0 <= value <= 1.0 for value in self.align):
            raise ValueError("GeneralContentView image alignment must stay inside 0..1")
        if self.clip_radius is not None and self.clip_radius < 0.0:
            raise ValueError("GeneralContentView image clip radius cannot be negative")
        if self.resource_policy == "fallback" and self.fallback is None:
            raise ValueError("a fallback image resource needs a fallback operation")
        if self.resource_policy != "fallback" and self.fallback is not None:
            raise ValueError("an image fallback is only valid with resource_policy='fallback'")


@dataclass(frozen=True, slots=True)
class GeneralTextOp:
    text: str
    pos: tuple[float, float]
    size: int
    fill: Color
    anchor: str | None = None
    font: GeneralFontRef = GENERAL_FONT


@dataclass(frozen=True, slots=True)
class GeneralSpriteChoiceOp:
    """First available Unity sprite, with a text fallback when none are mounted."""

    names: tuple[str, ...]
    rect: Rect
    tint: Tint | None = None
    sampling: Sampling = "lanczos"
    fallback_text: GeneralTextOp | None = None

    def __post_init__(self) -> None:
        if not self.names or any(not str(name).strip() for name in self.names):
            raise ValueError("a GeneralContentView sprite choice needs non-empty names")


@dataclass(frozen=True, slots=True)
class GeneralViewportOp:
    """A natural-size child canvas hard-cropped into a viewport on its parent.

    Pillow's character-rank scroll prefab draws every row into a 860×685 content image before
    cropping its first 420 pixels. Keeping both sizes in the shared operation makes adapters
    replay every child—including fully clipped rows—rather than silently pruning dependencies.
    """

    offset: tuple[float, float]
    viewport_size: tuple[int, int]
    content_size: tuple[int, int]
    children: tuple[GeneralPrefabOp, ...]

    def __post_init__(self) -> None:
        if min(*self.viewport_size, *self.content_size) <= 0:
            raise ValueError("a GeneralContentView viewport needs positive dimensions")
        if self.viewport_size[0] > self.content_size[0] or self.viewport_size[1] > self.content_size[1]:
            raise ValueError("a GeneralContentView viewport cannot exceed its content canvas")


GeneralPrefabOp: TypeAlias = (
    GeneralSpriteOp
    | GeneralSpriteChoiceOp
    | GeneralRoundedRectOp
    | GeneralAssetImageOp
    | GeneralTextOp
    | GeneralViewportOp
)


@dataclass(frozen=True, slots=True)
class GeneralPrefabDisplayList:
    file_name: str
    size: tuple[int, int]
    ops: tuple[GeneralPrefabOp, ...]


class GeneralTextMetrics(Protocol):
    """Font measurements required by the shared fit/wrap layout."""

    def text_bbox(
        self,
        text: str,
        font: GeneralFontRef,
        size: int,
    ) -> tuple[float, float, float, float]: ...


def rect_transform_box(
    parent_size: tuple[float, float],
    anchor_min: tuple[float, float],
    anchor_max: tuple[float, float],
    anchored_position: tuple[float, float],
    size_delta: tuple[float, float],
    pivot: tuple[float, float],
) -> Rect:
    """Resolve a Unity ``RectTransform`` into a top-left-origin rectangle."""

    parent_w, parent_h = parent_size
    ax0, ay0 = anchor_min
    ax1, ay1 = anchor_max
    pos_x, pos_y = anchored_position
    size_x, size_y = size_delta
    pivot_x, pivot_y = pivot
    width = (ax1 - ax0) * parent_w + size_x
    height = (ay1 - ay0) * parent_h + size_y
    anchor_ref_x = ax0 * parent_w + (ax1 - ax0) * parent_w * pivot_x
    anchor_ref_y = ay0 * parent_h + (ay1 - ay0) * parent_h * pivot_y
    pivot_unity_x = anchor_ref_x + pos_x
    pivot_unity_y = anchor_ref_y + pos_y
    left = pivot_unity_x - width * pivot_x
    bottom = pivot_unity_y - height * pivot_y
    return (left, parent_h - (bottom + height), left + width, parent_h - bottom)


def _text_size(metrics: GeneralTextMetrics, text: str, font: GeneralFontRef, size: int) -> tuple[float, float]:
    left, top, right, bottom = metrics.text_bbox(text, font, size)
    return right - left, bottom - top


def _fit_text_op(
    metrics: GeneralTextMetrics,
    box: Rect,
    text: str,
    *,
    max_size: int,
    min_size: int = 12,
    fill: Color,
    anchor: str = "lm",
    font: GeneralFontRef = GENERAL_FONT,
) -> GeneralTextOp:
    left, top, right, bottom = (round(value) for value in box)
    if anchor.startswith("r"):
        x = right
    elif anchor.startswith("m"):
        x = (left + right) // 2
    else:
        x = left

    for size in range(max_size, min_size - 1, -1):
        width, height = _text_size(metrics, text, font, size)
        if width <= right - left and height <= bottom - top:
            y = (top + bottom) // 2 if anchor.endswith("m") else top
            return GeneralTextOp(text, (x, y), size, fill, anchor, font)

    # Preserve PNGRenderer.draw_fit_text's historical fallback: even a non-middle anchor
    # receives the vertical midpoint when no candidate size fits.
    return GeneralTextOp(text, (x, (top + bottom) // 2), min_size, fill, anchor, font)


def _wrap_tokens(raw_line: str) -> list[str]:
    tokens: list[str] = []
    token = ""
    for char in raw_line:
        if char.isascii() and (char.isalnum() or char in "._-@:/#"):
            token += char
            continue
        if token:
            tokens.append(token)
            token = ""
        tokens.append(char)
    if token:
        tokens.append(token)
    return tokens


def _wrapped_text_width(
    metrics: GeneralTextMetrics,
    value: str,
    font: GeneralFontRef,
    size: int,
) -> float:
    return _text_size(metrics, value, font, size)[0]


def _append_wrapped_token(
    lines: list[str],
    line: str,
    token: str,
    metrics: GeneralTextMetrics,
    font: GeneralFontRef,
    size: int,
    max_width: int,
) -> str:
    if _wrapped_text_width(metrics, token, font, size) <= max_width:
        return line + token
    for char in token:
        trial = line + char
        if line and _wrapped_text_width(metrics, trial, font, size) > max_width:
            lines.append(line)
            line = char
        else:
            line = trial
    return line


def _wrap_text(
    metrics: GeneralTextMetrics,
    text: str,
    font: GeneralFontRef,
    size: int,
    max_width: int,
) -> list[str]:
    """The existing GeneralContentView greedy CJK/Latin wrapping, kept byte-for-byte in intent."""
    lines: list[str] = []
    for raw_line in text.splitlines() or [""]:
        line = ""
        for token in _wrap_tokens(raw_line):
            trial = line + token
            if line and _wrapped_text_width(metrics, trial, font, size) > max_width:
                lines.append(line)
                line = ""
            line = _append_wrapped_token(lines, line, token, metrics, font, size, max_width)
        lines.append(line)
    return lines


def _edit_user_name_ops(
    size: tuple[int, int],
    profile_context: Mapping[str, Any],
    metrics: GeneralTextMetrics,
    palette: GeneralPrefabPalette,
) -> list[GeneralPrefabOp]:
    base_rect = rect_transform_box(
        size,
        (0.0, 0.0),
        (1.0, 1.0),
        (0.0, 0.0),
        (0.0, 0.0),
        (0.5, 0.5),
    )
    name = str((profile_context.get("user") or {}).get("name", "") or "")
    name = "".join(char for char in name if char.isprintable())
    text_rect = rect_transform_box(
        size,
        (0.5, 0.5),
        (0.5, 0.5),
        (18.5, 0.0),
        (509.0, 32.0),
        (0.5, 0.5),
    )
    icon_rect = rect_transform_box(
        size,
        (1.0, 0.5),
        (1.0, 0.5),
        (-16.0, 0.0),
        (42.0, 42.0),
        (1.0, 0.5),
    )
    return [
        GeneralSpriteOp(
            "bg_base_r16_wh",
            base_rect,
            tint=palette.input_tint,
            sliced_border=(21, 21, 21, 21),
        ),
        _fit_text_op(metrics, text_rect, name, max_size=30, fill=palette.text),
        GeneralSpriteOp("icon_write_wh", icon_rect, tint=palette.dark_tint),
    ]


def _x_ops(
    size: tuple[int, int],
    profile_context: Mapping[str, Any],
    metrics: GeneralTextMetrics,
    palette: GeneralPrefabPalette,
) -> list[GeneralPrefabOp]:
    base_rect = rect_transform_box(size, (0.0, 0.0), (1.0, 1.0), (0.0, 0.0), (0.0, 0.0), (0.5, 0.5))
    icon_rect = rect_transform_box(size, (0.0, 0.5), (0.0, 0.5), (26.0, 0.0), (38.0, 38.0), (0.5, 0.5))
    twitter_id = str((profile_context.get("userProfile") or {}).get("twitterId", "") or "").strip()
    text = f"@{twitter_id.removeprefix('@')}" if twitter_id else ""
    text_rect = rect_transform_box(size, (0.5, 0.5), (0.5, 0.5), (26.0, 0.0), (430.0, 32.0), (0.5, 0.5))
    icon_size = max(18, round(icon_rect[3] - icon_rect[1]) - 8)
    return [
        GeneralSpriteOp(
            "bg_base_r16_wh",
            base_rect,
            tint=palette.input_tint,
            sliced_border=(21, 21, 21, 21),
        ),
        GeneralSpriteChoiceOp(
            ("x_icon", "icon_twitter_wh"),
            icon_rect,
            tint=palette.dark_tint,
            fallback_text=GeneralTextOp(
                "X",
                ((icon_rect[0] + icon_rect[2]) / 2.0, (icon_rect[1] + icon_rect[3]) / 2.0),
                icon_size,
                palette.text,
                "mm",
            ),
        ),
        _fit_text_op(metrics, text_rect, text, max_size=30, fill=palette.text),
    ]


def _comment_ops(
    size: tuple[int, int],
    profile_context: Mapping[str, Any],
    labels: Mapping[str, str],
    metrics: GeneralTextMetrics,
    palette: GeneralPrefabPalette,
) -> list[GeneralPrefabOp]:
    title_rect = rect_transform_box(
        size,
        (0.0, 1.0),
        (0.0, 1.0),
        (69.7, -13.5),
        (152.5, 32.0),
        (0.5, 0.5),
    )
    edit_rect = rect_transform_box(
        size,
        (0.0, 0.0),
        (1.0, 1.0),
        (0.0, -25.0),
        (0.0, -50.0),
        (0.5, 0.5),
    )
    field_rect = rect_transform_box(
        (edit_rect[2] - edit_rect[0], edit_rect[3] - edit_rect[1]),
        (0.0, 0.0),
        (1.0, 1.0),
        (-22.0, 0.0),
        (-76.0, -58.0),
        (0.5, 0.5),
    )
    field_rect = (
        field_rect[0] + edit_rect[0],
        field_rect[1] + edit_rect[1],
        field_rect[2] + edit_rect[0],
        field_rect[3] + edit_rect[1],
    )
    icon_rect = rect_transform_box(
        (edit_rect[2] - edit_rect[0], edit_rect[3] - edit_rect[1]),
        (1.0, 1.0),
        (1.0, 1.0),
        (-17.0, -11.0),
        (42.0, 42.0),
        (1.0, 1.0),
    )
    icon_rect = (
        icon_rect[0] + edit_rect[0],
        icon_rect[1] + edit_rect[1],
        icon_rect[2] + edit_rect[0],
        icon_rect[3] + edit_rect[1],
    )

    title = labels.get("comment_title", "comment_title")
    comment = str((profile_context.get("userProfile") or {}).get("word", "") or "")
    lines = _wrap_text(
        metrics,
        comment,
        GENERAL_FONT,
        30,
        max(1, round(field_rect[2] - field_rect[0])),
    )[:3]
    ops: list[GeneralPrefabOp] = [
        GeneralTextOp(
            title,
            ((title_rect[0] + title_rect[2]) / 2.0, (title_rect[1] + title_rect[3]) / 2.0),
            22,
            palette.label_text,
            "mm",
        ),
        GeneralSpriteOp(
            "bg_base_r16_wh",
            edit_rect,
            tint=palette.input_tint,
            sliced_border=(21, 21, 21, 21),
        ),
    ]
    y = round(field_rect[1])
    for line in lines:
        ops.append(GeneralTextOp(line, (round(field_rect[0]), y), 30, palette.text))
        y += 40
    ops.append(GeneralSpriteOp("icon_write_wh", icon_rect, tint=palette.dark_tint))
    return ops


def _total_power_ops(
    size: tuple[int, int],
    profile_context: Mapping[str, Any],
    labels: Mapping[str, str],
    metrics: GeneralTextMetrics,
    palette: GeneralPrefabPalette,
) -> list[GeneralPrefabOp]:
    title = labels.get("total_power", "total_power")
    title_bbox = metrics.text_bbox(title, GENERAL_FONT, 30)
    title_width = max(1, title_bbox[2] - title_bbox[0])
    title_left = -1.004974365234375
    title_center_y = size[1] / 2.0

    line_rect = rect_transform_box(
        size,
        (0.0, 0.5),
        (0.0, 0.5),
        (title_left + title_width + 14.004974365234375, 0.0),
        (4.0, 32.0),
        (0.5, 0.5),
    )
    icon_rect = rect_transform_box(
        size,
        (0.0, 0.5),
        (0.0, 0.5),
        (title_left + title_width + 50.004974365234375, 0.0),
        (36.0, 42.0),
        (0.5, 0.5),
    )
    value_rect = rect_transform_box(
        size,
        (0.0, 0.5),
        (0.0, 0.5),
        (title_left + title_width + 217.50497436523438, 0.0),
        (160.0, 64.0),
        (1.0, 0.5),
    )
    button_rect = rect_transform_box(
        size,
        (0.0, 0.5),
        (0.0, 0.5),
        (title_left + title_width + 267.60498046875, 0.0),
        (72.0, 72.0),
        (0.5, 0.5),
    )
    info_rect = rect_transform_box(
        (button_rect[2] - button_rect[0], button_rect[3] - button_rect[1]),
        (0.5, 0.5),
        (0.5, 0.5),
        (0.0, 1.0),
        (8.0, 30.0),
        (0.5, 0.5),
    )
    info_rect = (
        info_rect[0] + button_rect[0],
        info_rect[1] + button_rect[1],
        info_rect[2] + button_rect[0],
        info_rect[3] + button_rect[1],
    )

    total = profile_context.get("totalPower") or {}
    value = int(total.get("totalPower", 0) or 0) if isinstance(total, Mapping) else 0
    return [
        GeneralTextOp(title, (title_left, title_center_y), 30, palette.label_text, "lm"),
        GeneralSpriteOp("bg_base_wh", line_rect, tint=palette.total_line_tint),
        GeneralSpriteOp("icon_deckPower_wh", icon_rect, tint=palette.dark_tint),
        GeneralTextOp(
            str(value),
            (value_rect[2], (value_rect[1] + value_rect[3]) / 2.0),
            32,
            palette.text,
            "rm",
        ),
        GeneralSpriteOp("btn_circle_h56_wh", button_rect),
        GeneralSpriteOp("icon_infomation_wh", info_rect, tint=palette.dark_tint),
    ]


def _character_rank_map(profile_context: Mapping[str, Any]) -> dict[int, int]:
    result: dict[int, int] = {}
    for row in profile_context.get("userCharacters", []) or []:
        if isinstance(row, dict):
            character_id = int(row.get("characterId", 0) or 0)
            if character_id:
                result[character_id] = int(row.get("characterRank", 0) or 0)
    return result


def _character_rank_tab_ops(
    size: tuple[int, int],
    labels: Mapping[str, str],
    palette: GeneralPrefabPalette,
    *,
    scroll: bool,
) -> list[GeneralPrefabOp]:
    tab_width = 828.0 if scroll else 760.0
    left = (size[0] - tab_width) / 2.0
    top = 23.5 if scroll else 24.0
    bottom = top + 57.0
    middle = left + tab_width / 2.0
    return [
        GeneralSpriteOp(
            "bg_base_r16_wh",
            (left, top, left + tab_width, bottom),
            tint=palette.input_tint,
            sliced_border=(21, 21, 21, 21),
            resource_policy="optional",
        ),
        GeneralSpriteOp(
            "bg_base_r16_wh",
            (left, top, middle, bottom),
            tint=(244, 246, 252, 230),
            sliced_border=(21, 21, 21, 21),
            resource_policy="optional",
        ),
        GeneralTextOp(
            labels.get("character_rank_tab", "character_rank_tab"),
            ((left + middle) / 2.0, (top + bottom) / 2.0),
            27,
            palette.text,
            "mm",
        ),
        GeneralTextOp(
            labels.get("challenge_stage_tab", "challenge_stage_tab"),
            ((middle + left + tab_width) / 2.0, (top + bottom) / 2.0),
            27,
            (255, 255, 255, 230),
            "mm",
        ),
    ]


def _character_rank_cell_ops(
    center: tuple[float, float],
    character_id: int,
    rank: int,
    palette: GeneralPrefabPalette,
    asset_paths: Mapping[str, AssetPath | None],
) -> list[GeneralPrefabOp]:
    center_x, center_y = center
    tint = (0.266667, 0.866667, 1.0, 1.0)
    return [
        GeneralSpriteOp(
            "bg_base_round_h64_wh",
            (center_x - 82.5, center_y - 22.5, center_x + 97.5, center_y + 42.5),
            tint=tint,
            sliced_border=(37, 0, 37, 0),
            resource_policy="optional",
        ),
        GeneralSpriteOp(
            "bg_base_circle_h96_wh",
            (center_x - 97.5, center_y - 42.0, center_x - 13.5, center_y + 42.0),
            tint=tint,
            resource_policy="optional",
        ),
        GeneralAssetImageOp(
            f"character_rank_icon:{character_id}",
            asset_paths.get(f"character_rank_icon:{character_id}"),
            (center_x - 93.5, center_y - 38.0, center_x - 17.5, center_y + 38.0),
            resource_policy="optional",
        ),
        GeneralTextOp(
            str(rank),
            (center_x + 27.0, center_y + 11.0),
            31,
            palette.text,
            "mm",
        ),
    ]


def _character_rank_cells_ops(
    profile_context: Mapping[str, Any],
    palette: GeneralPrefabPalette,
    asset_paths: Mapping[str, AssetPath | None],
    *,
    scroll: bool,
) -> list[GeneralPrefabOp]:
    ranks = _character_rank_map(profile_context)
    viewport_left = CHARACTER_RANK_SCROLL_VIEWPORT[0]
    ops: list[GeneralPrefabOp] = []
    for index, (_nickname, character_id) in enumerate(CHARA_LIST):
        if character_id is None:
            continue
        column = index % 4
        row = index // 4
        center_x = CHARACTER_RANK_CELL_CENTER_X[column] - (viewport_left if scroll else 0.0)
        first_center_y = CHARACTER_RANK_SCROLL_FIRST_CENTER_Y if scroll else CHARACTER_RANK_NON_SCROLL_FIRST_CENTER_Y
        center_y = first_center_y + row * CHARACTER_RANK_ROW_STEP
        ops.extend(
            _character_rank_cell_ops(
                (center_x, center_y),
                character_id,
                ranks.get(character_id, 0),
                palette,
                asset_paths,
            )
        )
    return ops


def _vertical_scrollbar_ops(rect: Rect) -> list[GeneralPrefabOp]:
    left, top, right, bottom = rect
    handle_height = min(220.0, max(80.0, (bottom - top) * 0.28))
    return [
        GeneralSpriteOp(
            "bg_base_round_vertical_h6_wh",
            rect,
            tint=(0.333333, 0.333333, 0.466667, 0.2),
            sliced_border=(0, 5, 0, 5),
            resource_policy="optional",
        ),
        GeneralSpriteOp(
            "bg_base_round_vertical_h8_wh",
            (left - 1.0, top + 18.0, right + 1.0, top + 18.0 + handle_height),
            tint=(0.333333, 0.333333, 0.466667, 1.0),
            sliced_border=(0, 6, 0, 6),
            resource_policy="optional",
        ),
    ]


def story_favorite_key(story: Mapping[str, Any]) -> str:
    """Stable cloud-resource key used by both custom-profile render backends."""

    return f"{story.get('storyType', '')}:{story.get('storyId', '')}"


def story_favorite_asset_key(story: Mapping[str, Any]) -> str:
    """Display-list dependency key for one resolved story banner."""

    return f"story_favorite:{story_favorite_key(story)}"


def ordered_story_favorites(stories: list[Any]) -> list[dict[str, Any]]:
    """Preserve the service's historical share-slot ordering and invalid-row filtering."""

    items = [story for story in stories if isinstance(story, dict)]
    return sorted(items, key=lambda story: int(story.get("shareNo", story.get("share_no", 9999)) or 9999))


def story_favorite_title(
    story: Mapping[str, Any],
    story_resources: Mapping[str, Mapping[str, Any]],
) -> str:
    resource = story_resources.get(story_favorite_key(story)) or {}
    title = str(resource.get("title", "") or story.get("comment", "") or "").strip()
    if title:
        return title
    return f"{story.get('storyType', '')} #{story.get('storyId', '')}".strip()


def _story_favorite_ops(
    size: tuple[int, int],
    profile_context: Mapping[str, Any],
    labels: Mapping[str, str],
    metrics: GeneralTextMetrics,
    palette: GeneralPrefabPalette,
    asset_paths: Mapping[str, AssetPath | None],
    story_resources: Mapping[str, Mapping[str, Any]],
) -> list[GeneralPrefabOp] | None:
    stories = profile_context.get("userStoryFavorites") or []
    if not isinstance(stories, list):
        return None

    ops: list[GeneralPrefabOp] = [
        _fit_text_op(
            metrics,
            (47, 10, 547, 66),
            labels.get("story_favorite_title", "story_favorite_title"),
            max_size=30,
            min_size=18,
            fill=palette.text,
        ),
        GeneralSpriteOp(
            "bg_base_wh",
            (40, 66, size[0] - 40, 70),
            tint=palette.total_line_tint,
            resource_policy="optional",
        ),
    ]
    if not stories:
        ops.append(
            GeneralTextOp(
                labels.get("not_set", "not_set"),
                (size[0] / 2.0, size[1] / 2.0),
                22,
                palette.text,
                "mm",
            )
        )
        return ops

    card_width, card_height = 403, 172
    gap_x, gap_y = 24, 20
    start_x, start_y = 25, 92
    stories_in_order = ordered_story_favorites(stories)
    for index, story in enumerate(stories_in_order):
        column = index % 2
        row = index // 2
        left = start_x + column * (card_width + gap_x)
        top = start_y + row * (card_height + gap_y)
        rect = (left, top, left + card_width, top + card_height)
        asset_key = story_favorite_asset_key(story)
        banner_path = asset_paths.get(asset_key)
        if banner_path is not None:
            ops.extend(
                (
                    GeneralAssetImageOp(
                        asset_key,
                        banner_path,
                        rect,
                        sampling="lanczos",
                        fit="cover",
                        align=(0.5, 0.5),
                        clip_radius=10,
                        resource_policy="required",
                    ),
                    GeneralRoundedRectOp(
                        rect,
                        10,
                        None,
                        outline=(235, 242, 255, 210),
                        width=2,
                        round_coordinates=True,
                    ),
                )
            )
            continue

        ops.extend(
            (
                GeneralSpriteOp(
                    "bg_base_r16_wh",
                    rect,
                    tint=palette.input_tint,
                    sliced_border=(21, 21, 21, 21),
                    resource_policy="optional",
                ),
                _fit_text_op(
                    metrics,
                    (left + 18, top + 12, left + card_width - 18, top + card_height - 12),
                    story_favorite_title(story, story_resources),
                    max_size=24,
                    min_size=13,
                    fill=palette.text,
                ),
            )
        )

    if len(stories_in_order) > 8:
        ops.extend(_vertical_scrollbar_ops((size[0] - 23, 92, size[0] - 17, size[1] - 25)))
    return ops


def _character_rank_and_challenge_stage_ops(
    size: tuple[int, int],
    profile_context: Mapping[str, Any],
    labels: Mapping[str, str],
    palette: GeneralPrefabPalette,
    asset_paths: Mapping[str, AssetPath | None],
    *,
    scroll: bool,
) -> list[GeneralPrefabOp]:
    ops = _character_rank_tab_ops(size, labels, palette, scroll=scroll)
    cells = _character_rank_cells_ops(profile_context, palette, asset_paths, scroll=scroll)
    if not scroll:
        ops.extend(cells)
        return ops

    viewport_left, viewport_top, viewport_right, viewport_bottom = CHARACTER_RANK_SCROLL_VIEWPORT
    ops.append(
        GeneralViewportOp(
            offset=(viewport_left, viewport_top),
            viewport_size=(round(viewport_right - viewport_left), round(viewport_bottom - viewport_top)),
            content_size=CHARACTER_RANK_SCROLL_CONTENT_SIZE,
            children=tuple(cells),
        )
    )
    ops.extend(_vertical_scrollbar_ops((885, 104, 891, 524)))
    return ops


def _music_clear_count_map(profile_context: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for row in profile_context.get("userMusicDifficultyClearCount", []) or []:
        if not isinstance(row, dict):
            continue
        difficulty = str(row.get("musicDifficultyType", "") or "").lower()
        if difficulty:
            result[difficulty] = {
                "liveClear": int(row.get("liveClear", 0) or 0),
                "fullCombo": int(row.get("fullCombo", 0) or 0),
                "allPerfect": int(row.get("allPerfect", 0) or 0),
            }
    return result


def _difficulty_count_cell_ops(
    rect: Rect,
    label: str,
    color: Color,
    value: int,
    *,
    tag_h: float = 34.0,
) -> list[GeneralPrefabOp]:
    left, top, right, bottom = rect
    return [
        GeneralRoundedRectOp((left, top, right, top + tag_h), 6, color),
        GeneralTextOp(
            label,
            ((left + right) / 2.0, top + tag_h / 2.0),
            20,
            (255, 255, 255, 255),
            "mm",
        ),
        GeneralTextOp(
            str(value),
            ((left + right) / 2.0, (top + tag_h + 2 + bottom) / 2.0),
            28,
            (58, 65, 82, 255),
            "mm",
        ),
    ]


def _music_clear_value_strip_ops(
    rect: Rect,
    key: str,
    counts: Mapping[str, Mapping[str, int]],
    difficulties: tuple[tuple[str, str, Color], ...],
    *,
    cell_gap: float = 8.0,
    tag_h: float = 34.0,
) -> list[GeneralPrefabOp]:
    cell_count = len(difficulties)
    left, top, right, bottom = rect
    cell_w = (right - left - cell_gap * (cell_count - 1)) / cell_count
    ops: list[GeneralPrefabOp] = []
    for index, (difficulty, text, color) in enumerate(difficulties):
        x = left + index * (cell_w + cell_gap)
        value = int((counts.get(difficulty) or {}).get(key, 0) or 0)
        ops.extend(
            _difficulty_count_cell_ops(
                (x, top, x + cell_w, bottom),
                text,
                color,
                value,
                tag_h=tag_h,
            )
        )
    return ops


def _music_clear_row_ops(
    rect: Rect,
    label: str,
    key: str,
    counts: Mapping[str, Mapping[str, int]],
    difficulties: tuple[tuple[str, str, Color], ...],
    *,
    header_h: int = 54,
    value_inset_x: float = 15.0,
    value_top_gap: float = 9.0,
) -> list[GeneralPrefabOp]:
    left, top, right, bottom = rect
    header_rect = (left, top, right, top + header_h)
    fallback = GeneralRoundedRectOp(
        tuple(round(value) for value in header_rect),
        12,
        (167, 167, 188, 220),
    )
    ops: list[GeneralPrefabOp] = [
        GeneralSpriteOp(
            "bg_base_r16_wh",
            header_rect,
            tint=(0.654902, 0.654902, 0.737255, 1.0),
            sliced_border=(21, 21, 21, 21),
            resource_policy="fallback",
            fallback=fallback,
        ),
        GeneralTextOp(
            label,
            ((left + right) / 2.0, top + header_h / 2.0),
            min(31, max(20, header_h - 12)),
            (255, 255, 255, 255),
            "mm",
        ),
    ]
    ops.extend(
        _music_clear_value_strip_ops(
            (left + value_inset_x, top + header_h + value_top_gap, right - value_inset_x, bottom),
            key,
            counts,
            difficulties,
        )
    )
    return ops


def _multi_live_ops(
    size: tuple[int, int],
    profile_context: Mapping[str, Any],
    labels: Mapping[str, str],
    metrics: GeneralTextMetrics,
    palette: GeneralPrefabPalette,
) -> list[GeneralPrefabOp] | None:
    data = profile_context.get("userMultiLiveTopScoreCount") or {}
    if not isinstance(data, dict):
        return None

    ops: list[GeneralPrefabOp] = [
        _fit_text_op(
            metrics,
            (20, 16, 280, 52),
            labels.get("multi_live_title", "multi_live_title"),
            max_size=30,
            min_size=18,
            fill=palette.text,
            anchor="lm",
        ),
        GeneralSpriteOp(
            "bg_base_wh",
            (34, 62, size[0] - 34, 66),
            tint=palette.total_line_tint,
            resource_policy="optional",
        ),
    ]

    def append_stat(
        root_center_x: float,
        root_center_y: float,
        label: str,
        value: int,
        *,
        label_width: float = 130.0,
        value_width: float = 142.0,
    ) -> None:
        label_left = root_center_x
        label_top = root_center_y - 28.0
        label_rect = (label_left, label_top, label_left + label_width, label_top + 56.0)
        ops.append(
            GeneralSpriteOp(
                "bg_base_r16_wh",
                label_rect,
                tint=palette.input_tint,
                sliced_border=(21, 21, 21, 21),
                resource_policy="optional",
            )
        )
        ops.append(
            _fit_text_op(
                metrics,
                (label_rect[0] + 10.0, label_rect[1] + 2.0, label_rect[2] - 10.0, label_rect[3] - 2.0),
                label,
                max_size=29,
                min_size=17,
                fill=(255, 255, 255, 255),
                anchor="mm",
            )
        )
        value_center_x = root_center_x + (210.0 if label == "MVP" else 207.0)
        ops.append(
            _fit_text_op(
                metrics,
                (
                    value_center_x - value_width / 2.0,
                    root_center_y - 30.0,
                    value_center_x + value_width / 2.0,
                    root_center_y + 30.0,
                ),
                f"{value}{labels.get('multi_live_count_suffix', 'multi_live_count_suffix')}",
                max_size=30,
                min_size=18,
                fill=palette.text,
                anchor="mm",
            )
        )

    append_stat(26.0, 118.0, "MVP", int(data.get("mvp", 0) or 0))
    append_stat(399.0, 118.0, "SUPER\nSTAR", int(data.get("superStar", 0) or 0))
    return ops


def _challenge_live_ops(
    size: tuple[int, int],
    profile_context: Mapping[str, Any],
    labels: Mapping[str, str],
    metrics: GeneralTextMetrics,
    palette: GeneralPrefabPalette,
    asset_paths: Mapping[str, AssetPath | None],
) -> list[GeneralPrefabOp] | None:
    data = profile_context.get("userChallengeLiveSoloResult") or {}
    if not isinstance(data, dict):
        return None
    character_id = int(data.get("characterId", 0) or 0)
    high_score = int(data.get("highScore", 0) or 0)
    if character_id <= 0 and high_score <= 0:
        return None

    solo_rect = (24, 96, 136, 144)
    return [
        GeneralSpriteOp(
            "bg_base_r16_wh",
            (0.0, 0.0, float(size[0]), float(size[1])),
            tint=(225, 238, 239, 205),
            sliced_border=(21, 21, 21, 21),
            resource_policy="optional",
        ),
        _fit_text_op(
            metrics,
            (28, 18, size[0] - 28, 58),
            labels.get("challenge_live_title", "challenge_live_title"),
            max_size=28,
            min_size=18,
            fill=palette.text,
            anchor="lm",
        ),
        GeneralSpriteOp(
            "bg_base_wh",
            (24, 62, size[0] - 22, 66),
            tint=palette.total_line_tint,
            resource_policy="optional",
        ),
        GeneralSpriteOp(
            "bg_base_r16_wh",
            solo_rect,
            tint=(169, 171, 205, 235),
            sliced_border=(21, 21, 21, 21),
            resource_policy="optional",
        ),
        GeneralTextOp(
            labels.get("challenge_live_solo", "challenge_live_solo"),
            ((solo_rect[0] + solo_rect[2]) / 2.0, (solo_rect[1] + solo_rect[3]) / 2.0),
            25,
            (255, 255, 255, 255),
            "mm",
        ),
        GeneralAssetImageOp(
            "challenge_character_icon",
            asset_paths.get("challenge_character_icon"),
            (158, 86, 222, 150),
            resource_policy="optional",
        ),
        _fit_text_op(
            metrics,
            (244, 92, size[0] - 30, 148),
            f"{high_score}",
            max_size=31,
            min_size=20,
            fill=palette.text,
            anchor="lm",
        ),
    ]


def _music_clear_info_ops(
    size: tuple[int, int],
    profile_context: Mapping[str, Any],
    labels: Mapping[str, str],
    difficulties: tuple[tuple[str, str, Color], ...],
) -> list[GeneralPrefabOp]:
    rows = (
        (labels.get("music_clear", "music_clear"), "liveClear"),
        (labels.get("music_full_combo", "music_full_combo"), "fullCombo"),
    )
    counts = _music_clear_count_map(profile_context)
    row_gap = 20
    row_h = (size[1] - row_gap * (len(rows) - 1)) / len(rows)
    ops: list[GeneralPrefabOp] = []
    for index, (label, key) in enumerate(rows):
        top = index * (row_h + row_gap)
        ops.extend(
            _music_clear_row_ops(
                (0, top, size[0], top + row_h),
                label,
                key,
                counts,
                difficulties,
                header_h=54,
                value_inset_x=14,
                value_top_gap=18,
            )
        )
    return ops


def _music_clear_select_tab_info_ops(
    size: tuple[int, int],
    profile_context: Mapping[str, Any],
    labels: Mapping[str, str],
    difficulties: tuple[tuple[str, str, Color], ...],
    palette: GeneralPrefabPalette,
) -> list[GeneralPrefabOp]:
    tab_rect = (26, 0, size[0] - 22, 50)
    segment_w = (tab_rect[2] - tab_rect[0]) / 3.0
    selected_rect = (tab_rect[0], tab_rect[1], tab_rect[0] + segment_w, tab_rect[3])
    ops: list[GeneralPrefabOp] = [
        GeneralSpriteOp(
            "bg_base_r16_wh",
            tab_rect,
            tint=palette.input_tint,
            sliced_border=(21, 21, 21, 21),
            resource_policy="optional",
        ),
        GeneralSpriteOp(
            "bg_base_r16_wh",
            selected_rect,
            tint=(244, 246, 252, 230),
            sliced_border=(21, 21, 21, 21),
            resource_policy="optional",
        ),
    ]
    tab_labels = (
        labels.get("music_clear", "music_clear"),
        labels.get("music_full_combo", "music_full_combo"),
        labels.get("music_all_perfect", "music_all_perfect"),
    )
    for index, label in enumerate(tab_labels):
        ops.append(
            GeneralTextOp(
                label,
                (tab_rect[0] + segment_w * (index + 0.5), 25.0),
                23,
                palette.text if index == 0 else (255, 255, 255, 245),
                "mm",
            )
        )
    for index in (1, 2):
        x = tab_rect[0] + segment_w * index
        ops.append(GeneralRoundedRectOp((x - 2, 10, x + 2, 41), 2, (116, 122, 142, 130)))
    append_separator_x = size[0] - 142
    ops.append(
        GeneralRoundedRectOp(
            (append_separator_x - 1, 75, append_separator_x + 1, 154),
            1,
            (203, 106, 211, 180),
        )
    )
    ops.extend(
        _music_clear_value_strip_ops(
            (26, 74, size[0] - 22, 158),
            "liveClear",
            _music_clear_count_map(profile_context),
            difficulties,
            cell_gap=8,
            tag_h=34,
        )
    )
    return ops


def build_general_prefab_display_list(
    file_name: str,
    *,
    size: tuple[int, int],
    profile_context: Mapping[str, Any],
    labels: Mapping[str, str],
    metrics: GeneralTextMetrics,
    palette: GeneralPrefabPalette,
    asset_paths: Mapping[str, AssetPath | None] | None = None,
    music_difficulties: tuple[tuple[str, str, Color], ...] = (),
    story_favorite_resources: Mapping[str, Mapping[str, Any]] | None = None,
) -> GeneralPrefabDisplayList | None:
    """Build one supported GeneralContentView display list.

    Unsupported prefabs raise ``ValueError`` rather than returning a partial list.  Callers
    should only dispatch names migrated into this shared seam.  ``None`` is a deliberate
    compatibility result for prefabs whose historical composer is a no-op for missing data.
    """

    asset_paths = asset_paths or {}
    story_favorite_resources = story_favorite_resources or {}
    if file_name == "X":
        ops = _x_ops(size, profile_context, metrics, palette)
    elif file_name == "EditUserName":
        ops = _edit_user_name_ops(size, profile_context, metrics, palette)
    elif file_name == "Comment":
        ops = _comment_ops(size, profile_context, labels, metrics, palette)
    elif file_name == "TotalPower":
        ops = _total_power_ops(size, profile_context, labels, metrics, palette)
    elif file_name == "MultiLive":
        ops = _multi_live_ops(size, profile_context, labels, metrics, palette)
    elif file_name == "ChallengeLive":
        ops = _challenge_live_ops(size, profile_context, labels, metrics, palette, asset_paths)
    elif file_name == "CharacterRankAndChallengeStage":
        ops = _character_rank_and_challenge_stage_ops(
            size,
            profile_context,
            labels,
            palette,
            asset_paths,
            scroll=False,
        )
    elif file_name == "CharacterRankAndChallengeStageScroll":
        ops = _character_rank_and_challenge_stage_ops(
            size,
            profile_context,
            labels,
            palette,
            asset_paths,
            scroll=True,
        )
    elif file_name == "MusicClearInfo":
        ops = _music_clear_info_ops(size, profile_context, labels, music_difficulties)
    elif file_name == "MusicClearSelectTabInfo":
        ops = _music_clear_select_tab_info_ops(size, profile_context, labels, music_difficulties, palette)
    elif file_name == "StoryFavorite":
        ops = _story_favorite_ops(
            size,
            profile_context,
            labels,
            metrics,
            palette,
            asset_paths,
            story_favorite_resources,
        )
    else:
        raise ValueError(f"unsupported shared GeneralContentView prefab: {file_name}")
    if ops is None:
        return None
    return GeneralPrefabDisplayList(file_name, size, tuple(ops))


PillowFontFactory: TypeAlias = Callable[[int, bool], ImageFont.ImageFont]
PillowAssetLoader: TypeAlias = Callable[[Path | None], Image.Image | None]


class PillowSpritePaster(Protocol):
    def __call__(
        self,
        image: Image.Image,
        name: str,
        rect: Rect,
        *,
        tint: Tint | None = None,
        sliced_border: tuple[int, int, int, int] | None = None,
        resample: Image.Resampling = Image.Resampling.LANCZOS,
    ) -> bool: ...


class PillowGeneralPrefabAdapter(GeneralTextMetrics):
    """Pillow metrics + display-list replay used by the compatibility renderer."""

    _RESAMPLING: Mapping[Sampling, Image.Resampling] = {
        "nearest": Image.Resampling.NEAREST,
        "bilinear": Image.Resampling.BILINEAR,
        "bicubic": Image.Resampling.BICUBIC,
        "lanczos": Image.Resampling.LANCZOS,
    }

    def __init__(
        self,
        font_factory: PillowFontFactory,
        sprite_paster: PillowSpritePaster,
        asset_loader: PillowAssetLoader | None = None,
    ) -> None:
        self._font_factory = font_factory
        self._sprite_paster = sprite_paster
        self._asset_loader = asset_loader
        self._fonts: dict[tuple[GeneralFontRef, int], ImageFont.ImageFont] = {}
        self._metric_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

    def _font(self, font: GeneralFontRef, size: int) -> ImageFont.ImageFont:
        key = (font, int(size))
        loaded = self._fonts.get(key)
        if loaded is None:
            loaded = self._font_factory(int(size), font.bold)
            self._fonts[key] = loaded
        return loaded

    def text_bbox(
        self,
        text: str,
        font: GeneralFontRef,
        size: int,
    ) -> tuple[float, float, float, float]:
        return self._metric_draw.textbbox((0, 0), text, font=self._font(font, size))

    def render(self, display_list: GeneralPrefabDisplayList) -> Image.Image:
        image = Image.new("RGBA", display_list.size, (0, 0, 0, 0))

        def replay(target: Image.Image, ops: tuple[GeneralPrefabOp, ...]) -> None:
            draw = ImageDraw.Draw(target)

            def draw_rounded_rect(op: GeneralRoundedRectOp) -> None:
                rect = tuple(round(value) for value in op.rect) if op.round_coordinates else op.rect
                draw.rounded_rectangle(
                    rect,
                    radius=op.radius,
                    fill=op.fill,
                    outline=op.outline,
                    width=op.width,
                )

            def missing_resource(
                resource: str,
                policy: ResourcePolicy,
                fallback: GeneralRoundedRectOp | None,
            ) -> None:
                if policy == "required":
                    raise FileNotFoundError(f"required GeneralContentView resource is missing: {resource}")
                if policy == "fallback":
                    if fallback is None:  # pragma: no cover - dataclass validation rejects this
                        raise RuntimeError(f"missing fallback operation for GeneralContentView resource: {resource}")
                    draw_rounded_rect(fallback)

            for op in ops:
                if isinstance(op, GeneralSpriteOp):
                    pasted = self._sprite_paster(
                        target,
                        op.name,
                        op.rect,
                        tint=op.tint,
                        sliced_border=op.sliced_border,
                        resample=self._RESAMPLING[op.sampling],
                    )
                    if not pasted:
                        missing_resource(op.name, op.resource_policy, op.fallback)
                    continue
                if isinstance(op, GeneralSpriteChoiceOp):
                    pasted = any(
                        self._sprite_paster(
                            target,
                            name,
                            op.rect,
                            tint=op.tint,
                            resample=self._RESAMPLING[op.sampling],
                        )
                        for name in op.names
                    )
                    if not pasted and op.fallback_text is not None:
                        fallback = op.fallback_text
                        draw.text(
                            fallback.pos,
                            fallback.text,
                            font=self._font(fallback.font, fallback.size),
                            fill=fallback.fill,
                            anchor=fallback.anchor,
                        )
                    continue
                if isinstance(op, GeneralRoundedRectOp):
                    draw_rounded_rect(op)
                    continue
                if isinstance(op, GeneralAssetImageOp):
                    path = Path(op.path) if op.path is not None else None
                    source = self._asset_loader(path) if self._asset_loader is not None else None
                    if source is None:
                        missing_resource(op.resource_key, op.resource_policy, op.fallback)
                        continue
                    left, top, right, bottom = op.rect
                    width = max(1, round(right - left))
                    height = max(1, round(bottom - top))
                    if op.fit == "cover":
                        scale = max(width / source.width, height / source.height)
                        resized = source.resize(
                            (
                                max(1, round(source.width * scale)),
                                max(1, round(source.height * scale)),
                            ),
                            self._RESAMPLING[op.sampling],
                        )
                        crop_left = round((resized.width - width) * op.align[0])
                        crop_top = round((resized.height - height) * op.align[1])
                        resized = resized.crop((crop_left, crop_top, crop_left + width, crop_top + height))
                    else:
                        resized = source.resize((width, height), self._RESAMPLING[op.sampling])
                    if op.clip_radius is not None:
                        mask = Image.new("L", resized.size, 0)
                        ImageDraw.Draw(mask).rounded_rectangle(
                            (0, 0, width - 1, height - 1),
                            radius=op.clip_radius,
                            fill=255,
                        )
                        resized.putalpha(ImageChops.multiply(resized.getchannel("A"), mask))
                    target.alpha_composite(resized, (round(left), round(top)))
                    continue
                if isinstance(op, GeneralViewportOp):
                    content = Image.new("RGBA", op.content_size, (0, 0, 0, 0))
                    replay(content, op.children)
                    viewport = content.crop((0, 0, op.viewport_size[0], op.viewport_size[1]))
                    target.alpha_composite(viewport, (round(op.offset[0]), round(op.offset[1])))
                    continue
                draw.text(
                    op.pos,
                    op.text,
                    font=self._font(op.font, op.size),
                    fill=op.fill,
                    anchor=op.anchor,
                )

        replay(image, display_list.ops)
        return image
