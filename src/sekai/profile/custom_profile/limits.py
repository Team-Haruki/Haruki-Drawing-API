"""Hard safety limits for user-provided custom-profile Unity scenes.

These checks intentionally run before either renderer.  They are not a replacement for
renderer-side allocation guards: the request does not carry source-asset dimensions, so the
last reliable check for a resize still lives next to the allocation.
"""

from __future__ import annotations

import math
from typing import Any

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
    "stamps",
    "shapes",
    "texts",
    "miniCharas",
    "screenFilters",
)


def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


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

            for numeric_key in ("alpha", "outlineAlpha", "outlineSize", "lineSpacing"):
                if numeric_key in item and item[numeric_key] is not None:
                    _finite_number(item[numeric_key], f"{label}.{numeric_key}")


def ensure_raster_size(size: tuple[int, int], *, max_pixels: int, label: str) -> tuple[int, int]:
    """Validate a raster allocation and return normalized integer dimensions."""

    width, height = int(size[0]), int(size[1])
    if width <= 0 or height <= 0:
        raise ValueError(f"{label} must have positive dimensions, got {width}x{height}")
    pixels = width * height
    if pixels > max_pixels:
        raise ValueError(f"{label} would allocate {width}x{height} ({pixels} pixels); limit is {max_pixels}")
    return width, height
