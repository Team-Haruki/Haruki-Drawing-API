"""Native raster adapter for the shared missing-asset recipe.

This is a generated source asset, not an endpoint layout. The neutral recipe is
also consumed by the legacy adapter; fonts, dimensions and geometry live once.
No Pillow imports are required even in a fresh interpreter.
"""

from importlib import import_module
import json
import math
from pathlib import Path

from src.sekai.base.image_source import EncodedImageRef, MissingImageRef
from src.sekai.base.placeholder import placeholder_recipe


class NativePlaceholderBytes(bytes):
    """Immutable generated pixels, safe to retain in a native fragment cache."""

    def __new__(cls, data, dependencies=()):
        value = super().__new__(cls, data)
        value.dependencies = dependencies
        return value


def render_placeholder(source: MissingImageRef) -> EncodedImageRef:
    from src.sekai.base.utils import get_image_asset_signature
    from src.settings import DEFAULT_BOLD_FONT, DEFAULT_HEAVY_FONT, FONT_DIR

    native = import_module("haruki_skia_renderer")
    directory = Path(FONT_DIR)
    candidates = [
        directory / f"{name}{suffix}"
        for name in (DEFAULT_HEAVY_FONT, DEFAULT_BOLD_FONT)
        for suffix in ("", ".ttf", ".otf")
    ]
    recipe = placeholder_recipe(source.variant)
    requests = [(text, size) for text, _center, size, _fill in recipe.texts]
    metrics = []
    path = directory / DEFAULT_HEAVY_FONT
    if requests:
        for path in candidates:
            if not path.is_file():
                continue
            try:
                metrics = native.measure_text_batch("", str(path), requests, engine="freetype_basic")
            except ValueError:
                continue
            break
        else:
            raise RuntimeError("missing-placeholder font is unavailable to the native renderer")

    width, height = recipe.size
    children = [{"type": "Rect", "pos": [0, 0], "size": recipe.size, "fill": recipe.background}]
    for bounds, radius, fill, outline, stroke in recipe.roundrects:
        left, top, right, bottom = bounds
        # Both rectangles are filled: the border is inside the inclusive recipe bounds.
        for inset, color in ((0, outline), (stroke, fill)):
            children.append(
                {
                    "type": "RoundRect",
                    "pos": [left + inset, top + inset],
                    "size": [right - left + 1 - 2 * inset, bottom - top + 1 - 2 * inset],
                    "radius": max(0, radius - inset),
                    "fill": color,
                }
            )
    for (x0, y0, x1, y1), color, stroke in recipe.lines:
        length = math.hypot(x1 - x0, y1 - y0)
        cosine, sine = (x1 - x0) / length, (y1 - y0) / length
        children.append(
            {
                "type": "Transform",
                "matrix": [cosine, -sine, x0, sine, cosine, y0],
                "children": [{"type": "Rect", "pos": [0, -stroke / 2], "size": [length, stroke], "fill": color}],
            }
        )
    for (text, center, size, fill), metric in zip(recipe.texts, metrics, strict=True):
        left, top, right, bottom = metric["pillow_bbox"]
        children.append(
            {
                "type": "Text",
                "text": text,
                "pos": [center[0] - (right - left) / 2 - left, center[1] - (bottom - top) / 2 - top + metric["ascent"]],
                "font": {"role": "default", "size": size},
                "engine": "freetype_basic",
                "baseline": "alphabetic",
                "fill": fill,
            }
        )
    scene = {
        "version": 2,
        "assets_base_dir": str(directory),
        "export_format": "png",
        "fonts": {"dir": str(directory), "default": str(path), "bold": str(path)},
        "canvas": {"width": width, "height": height},
        "root": {"type": "Group", "offset": [0, 0], "size": recipe.size, "children": children},
    }
    result = native.render_scene(json.dumps(scene).encode(), {})
    dependencies = tuple(
        (str(candidate.parent), candidate.name, get_image_asset_signature(candidate.parent, candidate.name))
        for candidate in candidates
    )
    return EncodedImageRef(
        data=NativePlaceholderBytes(result["image_bytes"], dependencies), size=recipe.size, mode="RGBA"
    )
