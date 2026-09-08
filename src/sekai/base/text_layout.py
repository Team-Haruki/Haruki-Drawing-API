"""Shared text layout measurements with lazy legacy emoji support."""

from __future__ import annotations

from typing import Any

import emoji

from src.core.pillow_telemetry import PILLOW_TOUCH_TEXT_METRIC, record_pillow_touch

from .font_metrics import NativeFontMetrics, get_layout_font
from .paint_types import Position, Size

Font = Any

# Text measurement dominates the render. Profiling one inventory/list request: Font.getsize was
# 6816 calls / 0.97s — 84% of the 1.15s it takes to walk the widget tree, and 7x the entire native
# Skia render (0.16s). 518 text nodes measured ~13 times each: every node re-measures the "哇"
# standard box for its baseline, and layout measures a string again at draw time.
#
# Measuring is a PURE function of (face, size, string), so cache it. The cache is keyed by the font
# FILE and size, not the font object, so all pool threads share the results while each keeps its own
# FreeTypeFont (sharing the object would serialize every measurement — see ir_builder's font cache).
_TEXT_BBOX_CACHE_MAX = 50_000
_text_bbox_cache: dict[tuple, tuple[int, int, int, int]] = {}
_text_emoji_size_cache: dict[tuple, Size] = {}


def _font_key(font: Font) -> tuple:
    """A cross-thread identity for a font: its file + size. Falls back to the object id for PIL's
    in-memory default face, which has no path."""
    path = getattr(font, "path", None)
    size = getattr(font, "size", None)
    return (
        (path, size, getattr(font, "layout_engine", None), getattr(font, "signature", None))
        if isinstance(path, str)
        else (id(font), size)
    )


def _measure_bbox(font: Font, text: str) -> tuple[int, int, int, int]:
    key = (_font_key(font), text)
    cached = _text_bbox_cache.get(key)
    if cached is not None:
        return cached
    bbox = font.getbbox(text)
    # A plain dict is enough: entries are immutable, a duplicate compute under a race is harmless,
    # and a lock here would re-serialize the very thing this cache exists to parallelize. The bound
    # matters because the keys carry request text; a wholesale clear is fine since a cold measure is
    # cheap and correctness never depends on a hit.
    if len(_text_bbox_cache) >= _TEXT_BBOX_CACHE_MAX:
        _text_bbox_cache.clear()
    _text_bbox_cache[key] = bbox
    return bbox


def get_text_size(font: Font, text: str) -> Size:
    has_emoji = emoji.emoji_count(text) > 0
    if not isinstance(font, NativeFontMetrics):
        record_pillow_touch(PILLOW_TOUCH_TEXT_METRIC)
    if has_emoji:
        from .emoji_layout import emoji_text_size

        key = (_font_key(font), text)
        cached = _text_emoji_size_cache.get(key)
        if cached is None:
            cached = emoji_text_size(font, text)
            if len(_text_emoji_size_cache) >= _TEXT_BBOX_CACHE_MAX:
                _text_emoji_size_cache.clear()
            _text_emoji_size_cache[key] = cached
        return cached
    bbox = _measure_bbox(font, text)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def get_text_offset(font: Font, text: str) -> Position:
    if not isinstance(font, NativeFontMetrics):
        record_pillow_touch(PILLOW_TOUCH_TEXT_METRIC)
    bbox = _measure_bbox(font, text)
    return bbox[0], bbox[1]


def ascender_top_to_painter_y(font_path: str, font_size: int, ascender_top_y: int) -> int:
    """Convert an ``ImageDraw.text`` y (its default ``"la"`` anchor = top of the ascender)
    into the y ``Painter.text`` expects (it anchors the baseline at ``y + ink-height("哇")``).

    The two differ by ``ascent - ink_height("哇")`` — 4px for the bold font at size 20 — so a
    layout constant lifted straight from the old ImageDraw code lands the text that much too
    high. The gap is font- and size-dependent, so derive it from the metrics rather than
    folding a fudge factor into the constant. Both backends agree: the Skia path resolves
    ``Painter.text``'s logical top through the same Pillow ink height (IRBuilder's
    ``cjk_top`` baseline)."""
    font = get_layout_font(font_path, font_size)
    return ascender_top_y + font.getmetrics()[0] - get_text_size(font, "哇")[1]
