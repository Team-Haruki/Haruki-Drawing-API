"""Render IR v2 renderers for the two hand-built card endpoints: card/list and card/box.

Unlike the other endpoints (which reuse the plot.py widget tree via the IRPainter shadow
layer), these build the Render IR directly with :class:`IRBuilder` — the original, pixel-tuned
Skia migration. Card-specific shared helpers live in ``card_common``; the general native
helpers (module loader, payload mapping, background hour) live in ``canvas``. Gated per
endpoint by ``use_skia_card_list`` / ``use_skia_card_box``.
"""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any

from src.core.heavy_render_pool import EncodedImagePayload
from src.sekai.base import CHARACTER_COLOR_CODE
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
from src.sekai.card.drawer import _build_card_box_cache_key, _build_card_list_cache_key
from src.sekai.card.model import CardBoxRequest, CardListRequest
from src.sekai.skia_renderer.canvas import (
    background_hour as _background_hour,
    load_native_renderer as _load_native_renderer,
    payload_from_native as _payload_from_native,
)
from src.sekai.skia_renderer.card_common import (
    BG_PADDING as _BG_PADDING,
    BOX_GROUP_SEP as _BOX_GROUP_SEP,
    GRID_PADDING as _GRID_PADDING,
    THUMB as _THUMB,
    TITLE_H as _TITLE_H,
    TITLE_SEP as _TITLE_SEP,
    WATERMARK_FALLBACK as _WATERMARK_FALLBACK,
    center_text as _center_text,
    get_skia_payload_cached,
    limited_icon_path as _limited_icon_path,
    notice_title as _notice_title,
    parse_color as _parse_color,
    put_skia_payload_cache,
    thumbnail as _thumbnail,
    thumbnail_to_ir as _thumbnail_to_ir,
    validate_asset_path as _validate_asset_path,
)
from src.sekai.skia_renderer.ir_builder import IRBuilder
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
        b.text("未上线", (4, _CARD_H - 8), "bold", 20, baseline="alphabetic", fill=(200, 0, 0, 255))

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
        b.shadowed_text(
            line,
            (width - _BG_PADDING, wm_y + i * (wm_font_size + WATERMARK_LINE_SEP)),
            "default",
            float(wm_font_size),
            shadow_offset=(WATERMARK_SHADOW_OFFSET, WATERMARK_SHADOW_OFFSET),
            shadow_color=(75, 75, 75, 255),
            align="right",
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
            return None
        raise

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


# ===========================================================================
# card/box
# ===========================================================================


def build_card_box_ir(rqd: CardBoxRequest) -> dict[str, Any]:
    character_icon_paths = {
        str(chara_id): _validate_asset_path(path, field=f"character_icon_paths[{chara_id}]")
        for chara_id, path in rqd.character_icon_paths.items()
    }
    character_color_codes = {
        str(chara_id): rqd.character_color_codes.get(chara_id) or CHARACTER_COLOR_CODE.get(chara_id, "#cccccc")
        for chara_id in rqd.character_icon_paths
    }

    return {
        "version": IR_VERSION,
        "assets_base_dir": str(ASSETS_BASE_DIR),
        "export_format": EXPORT_IMAGE_FORMAT,
        "jpg_quality": JPG_QUALITY,
        "timezone": rqd.timezone,
        "background_hour": _background_hour(),
        "title": rqd.title,
        "show_id": rqd.show_id,
        "show_box": rqd.show_box,
        "background_img_path": _validate_asset_path(rqd.background_img_path, field="background_img_path"),
        "watermark": {
            "enabled": True,
            "text": getattr(rqd, "watermark", None) or "",
        },
        "fonts": {
            "dir": str(FONT_DIR),
            "default": DEFAULT_FONT,
            "bold": DEFAULT_BOLD_FONT,
        },
        "icons": {
            "term_limited": _validate_asset_path(rqd.term_limited_icon_path, field="term_limited_icon_path"),
            "fes_limited": _validate_asset_path(rqd.fes_limited_icon_path, field="fes_limited_icon_path"),
        },
        "character_icon_paths": character_icon_paths,
        "character_color_codes": character_color_codes,
        "cards": [
            {
                "card_id": user_card.card.card_id,
                "character_id": user_card.card.character_id,
                "release_at": user_card.card.release_at or 0,
                "supply_type": user_card.card.supply_type or "",
                "rare": user_card.card.rare or "",
                "is_after_training": bool(user_card.card.is_after_training),
                "has_card": user_card.has_card,
                "thumbnail_info": [_thumbnail_to_ir(thumbnail) for thumbnail in (user_card.card.thumbnail_info or [])],
            }
            for user_card in rqd.cards
        ],
    }


def _round_half_away(v: float) -> int:
    # Match Rust f32::round (half away from zero) for the positive layout values.
    return math.floor(v + 0.5)


def _selected_box_thumbnail(card: dict[str, Any]) -> dict[str, Any] | None:
    ti = card["thumbnail_info"]
    if not ti:
        return None
    if len(ti) == 1:
        return ti[0]
    if card["is_after_training"]:
        return ti[1]
    return ti[0]


def _build_box_groups(ir: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for card in ir["cards"]:
        if ir["show_box"] and not card["has_card"]:
            continue
        if _selected_box_thumbnail(card) is None:
            continue
        chara_id = card.get("character_id")
        if chara_id is None:
            continue
        grouped.setdefault(chara_id, []).append(card)
    groups = []
    for chara_id in sorted(grouped):
        cards = sorted(grouped[chara_id], key=lambda c: (c["rare"], c["release_at"], c["card_id"]))
        groups.append({"chara_id": chara_id, "cards": cards})
    return groups


def _compute_box_layout(groups: list[dict[str, Any]], show_id: bool) -> dict[str, Any]:
    max_card_num = max((len(g["cards"]) for g in groups), default=0)
    best_height = 1
    best_value = math.inf
    for candidate in range(1, max(max_card_num, 1) + 1):
        max_height = 0
        for g in groups:
            max_height = max(max_height, min(len(g["cards"]), candidate))
        total_width = 0
        total = 0
        space = 0
        for g in groups:
            n = len(g["cards"])
            width = max((n + candidate - 1) // candidate, 1)
            total_width += width
            total += max_height * width
            space += max_height * width - n
        if total_width > 9:
            value = max(float(total_width), max_height * 0.5)
        else:
            value = max(total_width * 0.5, float(max_height))
        density = total / (total - space) if total > space else 1.0
        value *= density
        if value < best_value:
            best_height = candidate
            best_value = value

    total_width_cols = sum(max((len(g["cards"]) + best_height - 1) // best_height, 1) for g in groups)
    area = total_width_cols * (best_height + 4)
    start_area = 9.0 * 5.0
    end_area = 26.0 * 50.0
    interp = min(1.0, max(0.0, (area - start_area) / (end_area - start_area)))
    sep = float(_round_half_away(8.0 + (4.0 - 8.0) * interp))
    thumb_size = float(_round_half_away(100.0 + (48.0 - 100.0) * interp))
    item_height = thumb_size + (16.0 if show_id else 0.0)

    group_widths = []
    for g in groups:
        cols = max((len(g["cards"]) + best_height - 1) // best_height, 1)
        group_widths.append(thumb_size * cols + sep * max(0, cols - 1))

    if not group_widths:
        content_width = _GRID_PADDING * 2
    else:
        content_width = _GRID_PADDING * 2 + sum(group_widths) + _BOX_GROUP_SEP * (len(group_widths) - 1)

    max_group_height = thumb_size
    for g in groups:
        rows = max(min(len(g["cards"]), best_height), 1)
        group_h = thumb_size + _BOX_GROUP_SEP + sep + _BOX_GROUP_SEP + rows * item_height + sep * max(0, rows - 1)
        max_group_height = max(max_group_height, group_h)

    return {
        "best_height": best_height,
        "thumb_size": thumb_size,
        "sep": sep,
        "panel_width": max(content_width, 520.0),
        "panel_height": _GRID_PADDING * 2 + max_group_height,
        "group_widths": group_widths,
    }


def build_card_box_scene(rqd: CardBoxRequest) -> dict[str, Any]:
    """Build a Render IR v2 scene (the layout lives here; Rust only interprets)."""
    return _card_box_scene_from_ir(build_card_box_ir(rqd))


def _push_character_header(
    b: IRBuilder, ir: dict[str, Any], chara_id: int, x: float, y: float, size: float, width: float, sep: float
) -> None:
    key = str(chara_id)
    r, g, bl = _parse_color(ir["character_color_codes"].get(key) or "#cccccc")
    icon_path = ir["character_icon_paths"].get(key)
    if icon_path:
        b.image(icon_path, (x, y), (size, size), fit="stretch")
    else:
        b.roundrect((x, y), (size, size), 8, fill=(r, g, bl, 210))
    b.rect((x, y + size + _BOX_GROUP_SEP), (width, sep), fill=(r, g, bl, 255))


def _push_box_card(
    b: IRBuilder, card: dict[str, Any], icons: dict[str, Any], x: float, y: float, size: float, show_id: bool
) -> None:
    thumb = _selected_box_thumbnail(card)
    if thumb is not None:
        with b.group((x, y), (size, size)):
            _thumbnail(b, thumb, size)
    icon_path = _limited_icon_path(card["supply_type"], icons)
    if icon_path:
        icon_w = size * 0.75
        b.image(icon_path, (x + size - icon_w, y), (icon_w, 0), fit="width")
    if show_id:
        _center_text(b, str(card["card_id"]), "default", 12, x, y + size, size, 16, (0, 0, 0, 255))


def _card_box_scene_from_ir(ir: dict[str, Any]) -> dict[str, Any]:
    groups = _build_box_groups(ir)
    layout = _compute_box_layout(groups, ir["show_id"])
    has_title = bool(ir.get("title"))
    title_h = _TITLE_H + _TITLE_SEP if has_title else 0.0
    panel_width = layout["panel_width"]
    panel_h = layout["panel_height"]
    width = math.ceil(panel_width + _BG_PADDING * 2)
    height = math.ceil(_BG_PADDING * 2 + title_h + panel_h)

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
        _notice_title(b, _BG_PADDING, y, panel_width, ir["title"])
        y += _TITLE_H + _TITLE_SEP

    b.blurglass((_BG_PADDING, y), (panel_width, panel_h), 12, (255, 255, 255, 80), shadow_alpha=0.26)

    thumb_size = layout["thumb_size"]
    sep = layout["sep"]
    best_height = layout["best_height"]
    show_id = ir["show_id"]
    icons = ir["icons"]
    group_widths = layout["group_widths"]
    gx = _BG_PADDING + _GRID_PADDING
    gy = y + _GRID_PADDING
    for idx, group in enumerate(groups):
        group_w = group_widths[idx] if idx < len(group_widths) else thumb_size
        _push_character_header(b, ir, group["chara_id"], gx, gy, thumb_size, group_w, sep)
        item_h = thumb_size + (16.0 if show_id else 0.0)
        grid_y = gy + thumb_size + _BOX_GROUP_SEP + sep + _BOX_GROUP_SEP
        for card_idx, card in enumerate(group["cards"]):
            row = card_idx % best_height
            col = card_idx // best_height
            cx = gx + col * (thumb_size + sep)
            cy = grid_y + row * (item_h + sep)
            _push_box_card(b, card, icons, cx, cy, thumb_size, show_id)
        gx += group_w + _BOX_GROUP_SEP

    watermark = ir["watermark"]
    if watermark["enabled"]:
        text = watermark["text"] or _WATERMARK_FALLBACK
        b.text(text, (width - 150, height - 10), "default", 12, baseline="alphabetic", fill=(0, 0, 0, 120))

    return b.build()


def _box_cache_key(rqd: CardBoxRequest) -> str:
    return f"{_build_card_box_cache_key(rqd)}|skia|{EXPORT_IMAGE_FORMAT}|{JPG_QUALITY}"


async def render_card_box_payload(rqd: CardBoxRequest) -> EncodedImagePayload:
    cache_key = _box_cache_key(rqd)
    cached = get_skia_payload_cached(cache_key)
    if cached is not None:
        return cached
    native = _load_native_renderer()

    def _render():
        # Keep the scene build + JSON encode off the event loop (a 1000+ card box
        # would otherwise stall every concurrent request).
        # The layout is built in Python (Render IR v2); Rust render_scene is a pure interpreter.
        scene = build_card_box_scene(rqd)
        ir_json = json.dumps(scene, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return native.render_scene(ir_json)

    result = await run_in_pool(_render)
    if not isinstance(result, dict):
        raise SkiaCardRenderError("native renderer must return a dict")
    payload = _payload_from_native(result)
    put_skia_payload_cache(cache_key, payload, len(payload.image_bytes))
    return payload


# The card/box scene builder below predates main's collection-stats layout rework
# (commits 4a47140..2c2a008, +744 lines in card/drawer.py) and would render the old
# layout with the new stats silently missing. Enabling it is refused until the builder
# is reworked — plan: shim-first via the IRPainter shadow layer, see
# docs/skia-migration-restart-plan.md phase 7 (decision D5).
_CARD_BOX_SCENE_STALE = True


async def try_render_card_box_payload(rqd: CardBoxRequest) -> EncodedImagePayload | None:
    if not settings.drawing.use_skia_card_box:
        return None
    if _CARD_BOX_SCENE_STALE:
        logger.error(
            "use_skia_card_box is enabled but the Skia card/box scene builder is stale "
            "against main's collection-stats layout; refusing to render, falling back to "
            "Pillow (see docs/skia-migration-restart-plan.md)"
        )
        return None
    if rqd.user_info is not None:
        logger.info("card/box backend=skia skipped reason=user_info")
        return None

    started = time.perf_counter()
    try:
        payload = await render_card_box_payload(rqd)
    except Exception:
        elapsed = time.perf_counter() - started
        if settings.drawing.skia_card_fallback_to_pillow:
            logger.warning(
                "card/box backend=skia failed elapsed=%.3fs; falling back to pillow",
                elapsed,
                exc_info=True,
            )
            return None
        raise

    logger.info(
        "card/box backend=skia total=%.3fs encode=%.3fs bytes=%d image=%sx%s cards=%d",
        time.perf_counter() - started,
        payload.encode_elapsed,
        len(payload.image_bytes),
        payload.image_width,
        payload.image_height,
        len(rqd.cards),
    )
    return payload
