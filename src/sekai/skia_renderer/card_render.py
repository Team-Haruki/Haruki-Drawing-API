"""Render IR v2 renderer for the hand-built card/list endpoint.

Unlike the other endpoints (which reuse the plot.py widget tree via the IRPainter shadow
layer), this builds the Render IR directly with :class:`IRBuilder` — the original,
pixel-tuned Skia migration. card/box used to live here too, but its dedicated scene
builder (written to chase pixel parity with the pre-rework Pillow layout) was retired in
favor of the shadow layer once real-data parity held; see
``src.sekai.card.drawer.try_render_box_payload``. Card-specific shared helpers live in
``card_common``; the general native helpers live in ``canvas``. Gated by
``use_skia_card_list``.
"""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any

from src.core.debug import set_render_backend
from src.core.heavy_render_pool import EncodedImagePayload
from src.sekai.base.draw import (
    WATERMARK_BOTTOM_OFFSET,
    WATERMARK_LINE_SEP,
    WATERMARK_SHADOW_OFFSET,
    WATERMARK_TOP_OFFSET,
    build_request_watermark_text,
    get_watermark_render_spec,
)
from src.sekai.base.timezone import request_now
from src.sekai.base.utils import run_in_pool
from src.sekai.card.drawer import _build_card_list_cache_key
from src.sekai.card.model import CardListRequest
from src.sekai.skia_renderer.canvas import (
    background_hour as _background_hour,
    load_native_renderer as _load_native_renderer,
    payload_from_native as _payload_from_native,
)
from src.sekai.skia_renderer.card_common import (
    BG_PADDING as _BG_PADDING,
    GRID_PADDING as _GRID_PADDING,
    THUMB as _THUMB,
    TITLE_H as _TITLE_H,
    TITLE_SEP as _TITLE_SEP,
    WATERMARK_FALLBACK as _WATERMARK_FALLBACK,
    center_text as _center_text,
    get_skia_payload_cached,
    limited_icon_path as _limited_icon_path,
    notice_title as _notice_title,
    put_skia_payload_cache,
    thumbnail as _thumbnail,
    thumbnail_to_ir as _thumbnail_to_ir,
    validate_asset_path as _validate_asset_path,
)
from src.sekai.skia_renderer.ir_builder import IRBuilder
from src.sekai.skia_renderer.render_stats import (
    OUTCOME_DISABLED,
    OUTCOME_ERROR,
    OUTCOME_SKIA,
    backend_for_outcome,
    record_native_metrics,
    record_render,
    record_skia_cache_hit,
)
from src.settings import (
    ASSETS_BASE_DIR,
    DEFAULT_BOLD_FONT,
    DEFAULT_FONT,
    EXPORT_IMAGE_FORMAT,
    FONT_DIR,
    JPG_QUALITY,
    settings,
)

logger = logging.getLogger("card.draw.perf")

# card/list hand-builds its own IR (dedicated scene builder), so it never passes through
# render_canvas_payload and has to record its own outcomes — otherwise it is simply absent from
# /render-stats and every card/list request logs backend=pillow.
_CARD_LIST_ENDPOINT = "card_list"


def _record_card_list(outcome: str, payload=None) -> None:
    record_render(_CARD_LIST_ENDPOINT, outcome)
    backend = backend_for_outcome(outcome)
    set_render_backend(backend)
    if payload is not None:
        payload.backend = backend
        record_native_metrics(payload.native_metrics)


IR_VERSION = 1
NON_LIMITED_SUPPLY_TYPES = {"", "normal", "非限定"}


class SkiaCardRenderError(RuntimeError):
    pass


# ===========================================================================
# card/list
# ===========================================================================


def build_card_list_ir(rqd: CardListRequest) -> dict[str, Any]:
    cards_with_thumbs = [card for card in rqd.cards if card.thumbnail_info]
    cards_with_thumbs.sort(key=lambda card: (card.release_at or 0, card.card_id), reverse=True)

    skill_icon_paths = sorted(
        {
            path
            for card in cards_with_thumbs
            if card.skill
            and (path := _validate_asset_path(card.skill.skill_type_icon_path, field="skill_type_icon_path"))
        }
    )

    return {
        "version": IR_VERSION,
        "assets_base_dir": str(ASSETS_BASE_DIR),
        "export_format": EXPORT_IMAGE_FORMAT,
        "jpg_quality": JPG_QUALITY,
        "timezone": rqd.timezone,
        "now_ms": int(request_now(rqd.timezone).timestamp() * 1000),
        "background_hour": _background_hour(),
        "title": rqd.title,
        "background_img_path": _validate_asset_path(rqd.background_img_path, field="background_img_path"),
        "watermark": {
            "enabled": True,
            # Mirror Pillow's add_request_watermark(): "DT: <dt> (<tz>)  <default watermark>".
            "text": build_request_watermark_text(rqd),
        },
        "fonts": {
            "dir": str(FONT_DIR),
            "default": DEFAULT_FONT,
            "bold": DEFAULT_BOLD_FONT,
        },
        "icons": {
            "term_limited": _validate_asset_path(rqd.term_limited_icon_path, field="term_limited_icon_path"),
            "fes_limited": _validate_asset_path(rqd.fes_limited_icon_path, field="fes_limited_icon_path"),
            "skill": skill_icon_paths,
        },
        "cards": [
            {
                "card_id": card.card_id,
                "prefix": card.prefix or "",
                "release_at": card.release_at or 0,
                "supply_type": card.supply_type or "",
                "skill_type": card.skill.skill_type if card.skill else None,
                "skill_icon_path": _validate_asset_path(
                    card.skill.skill_type_icon_path if card.skill else None,
                    field="skill_type_icon_path",
                ),
                "thumbnail_info": [_thumbnail_to_ir(thumbnail) for thumbnail in (card.thumbnail_info or [])],
            }
            for card in cards_with_thumbs
        ],
    }


# Card List layout constants (mirror the Rust card_scene.rs / lib.rs values).
# Shared values (_BG_PADDING/_GRID_PADDING/_THUMB/_TITLE_*) come from card_common.
_PANEL_WIDTH = 996.0
_GRID_COLS = 3
_CARD_W = 316.0
_CARD_H = 190.0
_CARD_SEP = 8.0


def _is_non_limited(supply_type: str) -> bool:
    return supply_type.strip() in NON_LIMITED_SUPPLY_TYPES


def build_card_list_scene(rqd: CardListRequest) -> dict[str, Any]:
    """Build a Render IR v2 scene (the layout lives here; Rust only interprets)."""
    return _card_list_scene_from_ir(build_card_list_ir(rqd))


def _card_cell(b: IRBuilder, card: dict[str, Any], icons: dict[str, Any], now_ms: int) -> None:
    limited = not _is_non_limited(card["supply_type"])
    fill = (255, 250, 220, 200) if limited else (255, 255, 255, 80)
    b.blurglass((0, 0), (_CARD_W, _CARD_H), 10, fill, shadow_alpha=0.30)

    if card["release_at"] > now_ms:
        leak_top = _CARD_H - 26
        leak_baseline = b.painter_baseline_y(leak_top, "bold", 20)
        b.text("未上线", (6, leak_baseline), "bold", 20, baseline="alphabetic", fill=(200, 0, 0, 255))

    if card.get("skill_type") and card.get("skill_icon_path"):
        # Aspect-preserving (width 32), bottom-right anchored. Mirrors Pillow's
        # Frame(content_align="rb") + ImageBox(fit).set_w(32): right margin 8, bottom
        # margin 4 (Pillow's content frame sits a few px above the card's visual bottom).
        b.image(card["skill_icon_path"], (_CARD_W - 8, _CARD_H - 4), (32, 0), fit="width", anchor=(1, 1))

    thumbs = card["thumbnail_info"][:2]
    total_w = len(thumbs) * _THUMB + max(0, len(thumbs) - 1) * 16
    tx = (_CARD_W - total_w) / 2
    icon_path = _limited_icon_path(card["supply_type"], icons)
    for thumb in thumbs:
        b.shadow((tx, 16), (_THUMB, _THUMB), 8, alpha=0.35, offset=(2, 4), sigma=2.5)
        with b.group((tx, 16), (_THUMB, _THUMB)):
            _thumbnail(b, thumb, _THUMB)
        if icon_path:
            b.image(icon_path, (tx + _THUMB - 75, 16), (75, 0), fit="width")
        tx += _THUMB + 16

    _center_text(b, card["prefix"], "bold", 20, 0, 129, _CARD_W, 24, (0, 0, 0, 255))
    id_text = f"ID:{card['card_id']}"
    if limited:
        id_text += f"【{card['supply_type']}】"
    _center_text(b, id_text, "default", 20, 0, 158, _CARD_W, 24, (0, 0, 0, 255))


def _card_list_scene_from_ir(ir: dict[str, Any]) -> dict[str, Any]:
    cards = ir["cards"]
    has_title = bool(ir.get("title"))
    rows = math.ceil(max(1, len(cards)) / _GRID_COLS)
    title_h = _TITLE_H + _TITLE_SEP if has_title else 0.0
    grid_h = _GRID_PADDING * 2 + rows * _CARD_H + max(0, rows - 1) * _CARD_SEP

    # Pillow's add_watermark() (src/sekai/base/draw.py) swaps the canvas' bottom BG_PADDING
    # for a footer of TOP_OFFSET + wrapped watermark text height + SHADOW + BOTTOM_OFFSET.
    watermark = ir["watermark"]
    wm_font_size = 12
    wm_lines: list[str] = []
    if watermark["enabled"]:
        wm_text = watermark["text"] or _WATERMARK_FALLBACK
        wm_max_width = max(1, int(_PANEL_WIDTH) - WATERMARK_SHADOW_OFFSET)
        wm_font_size, wm_lines, _wm_w, wm_text_h = get_watermark_render_spec(wm_text, wm_max_width, 12)
        bottom_h = float(WATERMARK_TOP_OFFSET + wm_text_h + WATERMARK_SHADOW_OFFSET + WATERMARK_BOTTOM_OFFSET)
    else:
        bottom_h = _BG_PADDING

    width = math.ceil(_PANEL_WIDTH + _BG_PADDING * 2)
    height = math.ceil(_BG_PADDING + title_h + grid_h + bottom_h)

    fonts = ir["fonts"]
    b = IRBuilder(
        width,
        height,
        assets_base_dir=ir["assets_base_dir"],
        font_dir=fonts["dir"],
        default_font=fonts["default"],
        bold_font=fonts["bold"],
        export_format=ir["export_format"],
        jpg_quality=ir["jpg_quality"],
    )

    if ir.get("background_img_path"):
        b.image_bg(ir["background_img_path"], fade=0.1)
    else:
        b.triangle_bg(ir.get("background_hour") if ir.get("background_hour") is not None else 15.0)

    y = _BG_PADDING
    if has_title:
        _notice_title(b, _BG_PADDING, y, _PANEL_WIDTH, ir["title"])
        y += _TITLE_H + _TITLE_SEP

    b.blurglass((_BG_PADDING, y), (_PANEL_WIDTH, grid_h), 12, (255, 255, 255, 80), shadow_alpha=0.26)

    now_ms = ir["now_ms"]
    icons = ir["icons"]
    for idx, card in enumerate(cards):
        row = idx // _GRID_COLS
        col = idx % _GRID_COLS
        x = _BG_PADDING + _GRID_PADDING + col * (_CARD_W + _CARD_SEP)
        cy = y + _GRID_PADDING + row * (_CARD_H + _CARD_SEP)
        with b.group((x, cy), (_CARD_W, _CARD_H)):
            _card_cell(b, card, icons, now_ms)

    # Footer watermark: each line right-aligned to the content edge, white with a
    # (1, 1) grey shadow, starting TOP_OFFSET below the content (mirrors add_watermark()).
    wm_y = _BG_PADDING + title_h + grid_h + WATERMARK_TOP_OFFSET
    for i, line in enumerate(wm_lines):
        line_top = wm_y + i * (wm_font_size + WATERMARK_LINE_SEP)
        line_w, _ = b.measure_text_ink(line, "default", float(wm_font_size))
        b.shadowed_text(
            line,
            (width - _BG_PADDING - line_w, line_top),
            "default",
            float(wm_font_size),
            shadow_offset=(WATERMARK_SHADOW_OFFSET, WATERMARK_SHADOW_OFFSET),
            shadow_color=(75, 75, 75, 255),
            align="left",
            baseline="cjk_top",
            fill=(255, 255, 255, 255),
        )

    return b.build()


def _list_cache_key(rqd: CardListRequest) -> str:
    # Reuse Pillow's stable request key (no timestamp) and qualify by output format so the
    # cached encoded bytes match the current export settings.
    return f"{_build_card_list_cache_key(rqd)}|skia|{EXPORT_IMAGE_FORMAT}|{JPG_QUALITY}"


async def render_card_list_payload(rqd: CardListRequest) -> EncodedImagePayload:
    cache_key = _list_cache_key(rqd)
    cached = get_skia_payload_cached(cache_key)
    if cached is not None:
        record_skia_cache_hit(_CARD_LIST_ENDPOINT, cached)
        return cached
    native = _load_native_renderer()

    def _render():
        # Scene build + JSON encode are CPU work too — keep them off the event loop
        # (a large card list would otherwise stall every concurrent request).
        # The layout is built in Python (Render IR v2); Rust render_scene is a pure interpreter.
        scene = build_card_list_scene(rqd)
        ir_json = json.dumps(scene, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return native.render_scene(ir_json)

    result = await run_in_pool(_render)
    if not isinstance(result, dict):
        raise SkiaCardRenderError("native renderer must return a dict")
    payload = _payload_from_native(result)
    put_skia_payload_cache(cache_key, payload, len(payload.image_bytes))
    return payload


async def try_render_card_list_payload(rqd: CardListRequest) -> EncodedImagePayload | None:
    if not settings.drawing.use_skia_card_list:
        _record_card_list(OUTCOME_DISABLED)
        return None

    started = time.perf_counter()
    try:
        payload = await render_card_list_payload(rqd)
    except Exception:
        elapsed = time.perf_counter() - started
        if settings.drawing.skia_card_list_fallback_to_pillow:
            logger.warning(
                "card/list backend=skia failed elapsed=%.3fs; falling back to pillow",
                elapsed,
                exc_info=True,
            )
            _record_card_list(OUTCOME_ERROR)
            return None
        raise

    if payload.backend is None:  # a cache hit already stamped itself
        _record_card_list(OUTCOME_SKIA, payload)

    logger.info(
        "card/list backend=skia total=%.3fs encode=%.3fs bytes=%d image=%sx%s cards=%d",
        time.perf_counter() - started,
        payload.encode_elapsed,
        len(payload.image_bytes),
        payload.image_width,
        payload.image_height,
        len(rqd.cards),
    )
    return payload
