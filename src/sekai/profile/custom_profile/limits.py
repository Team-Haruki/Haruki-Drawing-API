"""Hard safety limits for user-provided custom-profile Unity scenes.

These checks intentionally run before either renderer.  They are not a replacement for
renderer-side allocation guards: the request does not carry source-asset dimensions, so the
last reliable check for a resize still lives next to the allocation.
"""

from __future__ import annotations

import math
from typing import Any

from src.sekai.profile.custom_profile.svg import TextStyle, parse_tmp_text

CONTENT_BUCKETS = (
    "generals",
    "generalBackgrounds",
    "storyBackgrounds",
    "standMembers",
    "cardMembers",
    "honors",
    "bondsHonors",
    "collections",
    "others",
    "characterIcons",
    "materials",
    "userInterfaceIcons",
    "stamps",
    "shapes",
    "texts",
    "miniCharas",
    "screenFilters",
)

# TMP tags in real cards intentionally exceed the outer editor fields for decorative effects.
# Keep compatibility headroom while still rejecting the orders-of-magnitude values that can
# turn one glyph into a multi-gigabyte raster. Renderer-side pixel guards remain authoritative.
_TMP_RICH_TEXT_SIZE_HEADROOM = 4.0
_TMP_RICH_TEXT_SCALE_HEADROOM = 8.0


class RasterSizeLimitError(ValueError):
    """A named raster allocation exceeded the configured per-layer pixel budget."""

    def __init__(self, *, label: str, width: int, height: int, max_pixels: int) -> None:
        self.label = label
        self.width = width
        self.height = height
        self.pixels = width * height
        self.max_pixels = max_pixels
        super().__init__(f"{label} would allocate {width}x{height} ({self.pixels} pixels); limit is {max_pixels}")


def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _validate_tmp_text_styles(
    text: str,
    base_size: float,
    *,
    label: str,
    max_scale: float,
    max_text_size: float,
) -> None:
    """Validate values after TMP rich-text tags have been applied.

    The request-level ``size`` and object scale are only defaults.  TMP ``<size>`` and
    ``<scale>`` tags replace them inside individual runs, so checking the outer fields alone
    still lets a small-looking request create a multi-gigabyte glyph raster.
    """

    base_style = TextStyle(
        color="#ffffff",
        alpha=1.0,
        size=base_size,
        scale_x=1.0,
        cspace=0.0,
        mspace=None,
        indent=0.0,
        line_indent=0.0,
        line_height=None,
        rotate=0.0,
        voffset=0.0,
        mark_color=None,
        bold=False,
        italic=False,
        underline=False,
        strike=False,
    )
    for token in parse_tmp_text(text, base_style):
        style = getattr(token, "style", None)
        if style is None:
            continue

        max_rich_text_size = max_text_size * _TMP_RICH_TEXT_SIZE_HEADROOM
        size = _finite_number(style.size, f"{label}.richText.size")
        if size <= 0 or size > max_rich_text_size:
            raise ValueError(f"{label}.richText.size must be within (0, {max_rich_text_size:g}]")

        max_rich_text_scale = max_scale * _TMP_RICH_TEXT_SCALE_HEADROOM
        scale_x = _finite_number(style.scale_x, f"{label}.richText.scale")
        if abs(scale_x) > max_rich_text_scale:
            raise ValueError(
                f"{label}.richText.scale must be within [-{max_rich_text_scale:g}, {max_rich_text_scale:g}]"
            )

        _finite_number(style.alpha, f"{label}.richText.alpha")
        _finite_number(style.rotate, f"{label}.richText.rotate")
        max_layout_magnitude = max_rich_text_size * max_rich_text_scale
        for key in (
            "cspace",
            "mspace",
            "indent",
            "indent_percent",
            "line_indent",
            "line_indent_percent",
            "line_height",
            "voffset",
            "pos",
            "pos_percent",
        ):
            value = getattr(style, key)
            if value is not None:
                number = _finite_number(value, f"{label}.richText.{key}")
                if abs(number) > max_layout_magnitude:
                    raise ValueError(
                        f"{label}.richText.{key} must be within [-{max_layout_magnitude:g}, {max_layout_magnitude:g}]"
                    )


def validate_custom_profile_card(
    card: dict[str, Any],
    *,
    max_elements: int,
    max_scale: float,
    max_text_size: float,
    max_text_length: int,
) -> None:
    """Reject scene shapes that can make either backend allocate without a useful bound."""

    layout = card.get("customProfileCard")
    if not isinstance(layout, dict):
        raise ValueError("card.customProfileCard must be an object")

    element_count = 0
    for bucket in CONTENT_BUCKETS:
        items = layout.get(bucket, [])
        if items is None:
            continue
        if not isinstance(items, list):
            raise ValueError(f"card.customProfileCard.{bucket} must be an array")
        element_count += len(items)
        if element_count > max_elements:
            raise ValueError(f"custom profile has {element_count} elements; limit is {max_elements}")

        for index, item in enumerate(items):
            label = f"card.customProfileCard.{bucket}[{index}]"
            if not isinstance(item, dict):
                raise ValueError(f"{label} must be an object")
            object_data = item.get("objectData")
            if not isinstance(object_data, dict):
                raise ValueError(f"{label}.objectData must be an object")

            scale = object_data.get("scale") or {}
            if not isinstance(scale, dict):
                raise ValueError(f"{label}.objectData.scale must be an object")
            sx = _finite_number(scale.get("x", 1.0), f"{label}.objectData.scale.x")
            sy = _finite_number(scale.get("y", sx), f"{label}.objectData.scale.y")
            if sx <= 0 or sy <= 0 or sx > max_scale or sy > max_scale:
                raise ValueError(f"{label}.objectData.scale must be within (0, {max_scale:g}] on both axes")

            for group in ("position", "rotation"):
                values = object_data.get(group) or {}
                if not isinstance(values, dict):
                    raise ValueError(f"{label}.objectData.{group} must be an object")
                for axis, value in values.items():
                    _finite_number(value, f"{label}.objectData.{group}.{axis}")

            if bucket == "texts":
                text = str(item.get("text", "") or "")
                if len(text) > max_text_length:
                    raise ValueError(f"{label}.text has {len(text)} characters; limit is {max_text_length}")
                size = _finite_number(item.get("size", 1.0), f"{label}.size")
                if size <= 0 or size > max_text_size:
                    raise ValueError(f"{label}.size must be within (0, {max_text_size:g}]")
                _validate_tmp_text_styles(
                    text,
                    size,
                    label=label,
                    max_scale=max_scale,
                    max_text_size=max_text_size,
                )

            for numeric_key in ("alpha", "outlineAlpha", "outlineSize", "lineSpacing"):
                if numeric_key in item and item[numeric_key] is not None:
                    number = _finite_number(item[numeric_key], f"{label}.{numeric_key}")
                    if bucket == "texts" and numeric_key == "lineSpacing":
                        max_line_spacing = (
                            max_text_size * _TMP_RICH_TEXT_SIZE_HEADROOM * max_scale * _TMP_RICH_TEXT_SCALE_HEADROOM
                        )
                        if abs(number) > max_line_spacing:
                            raise ValueError(
                                f"{label}.lineSpacing must be within [-{max_line_spacing:g}, {max_line_spacing:g}]"
                            )


def ensure_raster_size(size: tuple[int, int], *, max_pixels: int, label: str) -> tuple[int, int]:
    """Validate a raster allocation and return normalized integer dimensions."""

    try:
        width, height = int(size[0]), int(size[1])
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must have finite integer dimensions") from exc
    if width <= 0 or height <= 0:
        raise ValueError(f"{label} must have positive dimensions, got {width}x{height}")
    pixels = width * height
    if pixels > max_pixels:
        raise RasterSizeLimitError(label=label, width=width, height=height, max_pixels=max_pixels)
    return width, height
