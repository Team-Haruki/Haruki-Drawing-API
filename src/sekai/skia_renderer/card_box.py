from __future__ import annotations

from datetime import datetime
import importlib
import json
import logging
import math
import os
from pathlib import PurePosixPath, PureWindowsPath
import time
from typing import Any

from src.core.heavy_render_pool import EncodedImagePayload
from src.sekai.base import CHARACTER_COLOR_CODE
from src.sekai.base.utils import run_in_pool
from src.sekai.card.drawer import _build_card_box_cache_key
from src.sekai.card.model import CardBoxRequest
from src.sekai.skia_renderer.card_common import (
    BG_PADDING as _BG_PADDING,
    BOX_GROUP_SEP as _BOX_GROUP_SEP,
    GRID_PADDING as _GRID_PADDING,
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


class SkiaCardBoxRenderError(RuntimeError):
    pass


def _validate_asset_path(path: str | None, *, field: str) -> str | None:
    if path is None or path == "":
        return None
    if "\\" in path:
        raise ValueError(f"{field} must use forward slash asset paths")
    posix = PurePosixPath(path)
    windows = PureWindowsPath(path)
    if posix.is_absolute() or windows.is_absolute():
        raise ValueError(f"{field} must be relative to assets_base_dir")
    if ".." in posix.parts:
        raise ValueError(f"{field} must not contain '..'")
    return path


def _thumbnail_to_ir(thumbnail: Any) -> dict[str, Any]:
    rare_img_path = thumbnail.birthday_icon_path if thumbnail.rare == "rarity_birthday" else thumbnail.rare_img_path
    return {
        "card_id": thumbnail.card_id,
        "card_thumbnail_path": _validate_asset_path(thumbnail.card_thumbnail_path, field="card_thumbnail_path"),
        "rare": thumbnail.rare,
        "frame_img_path": _validate_asset_path(thumbnail.frame_img_path, field="frame_img_path"),
        "attr_img_path": _validate_asset_path(thumbnail.attr_img_path, field="attr_img_path"),
        "rare_img_path": _validate_asset_path(rare_img_path, field="rare_img_path"),
        "train_rank": thumbnail.train_rank,
        "train_rank_img_path": _validate_asset_path(thumbnail.train_rank_img_path, field="train_rank_img_path"),
        "level": thumbnail.level,
        "custom_text": thumbnail.custom_text,
        "is_after_training": thumbnail.is_after_training,
        "is_pcard": thumbnail.is_pcard,
    }


def _background_hour() -> float:
    override_hour = os.getenv("HARUKI_BG_TEST_HOUR")
    if override_hour is not None:
        try:
            return max(0.0, min(23.999, float(override_hour)))
        except ValueError:
            pass
    now = datetime.now()
    return now.hour + now.minute / 60 + now.second / 3600


def build_card_box_ir(rqd: CardBoxRequest) -> dict[str, Any]:
    character_icon_paths = {
        str(chara_id): _validate_asset_path(path, field=f"character_icon_paths[{chara_id}]")
        for chara_id, path in rqd.character_icon_paths.items()
    }
    character_color_codes = {
        str(chara_id): rqd.character_color_codes.get(chara_id)
        or CHARACTER_COLOR_CODE.get(chara_id, "#cccccc")
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
                "thumbnail_info": [
                    _thumbnail_to_ir(thumbnail) for thumbnail in (user_card.card.thumbnail_info or [])
                ],
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


def _push_character_header(b: IRBuilder, ir: dict[str, Any], chara_id: int, x: float, y: float, size: float,
                           width: float, sep: float) -> None:
    key = str(chara_id)
    r, g, bl = _parse_color(ir["character_color_codes"].get(key) or "#cccccc")
    icon_path = ir["character_icon_paths"].get(key)
    if icon_path:
        b.image(icon_path, (x, y), (size, size), fit="stretch")
    else:
        b.roundrect((x, y), (size, size), 8, fill=(r, g, bl, 210))
    b.rect((x, y + size + _BOX_GROUP_SEP), (width, sep), fill=(r, g, bl, 255))


def _push_box_card(b: IRBuilder, card: dict[str, Any], icons: dict[str, Any], x: float, y: float, size: float,
                   show_id: bool) -> None:
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
        b.image_bg(ir["background_img_path"])
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


def _load_native_renderer():
    try:
        return importlib.import_module("haruki_skia_renderer")
    except ImportError as exc:
        raise SkiaCardBoxRenderError("haruki_skia_renderer is not installed") from exc


def _payload_from_native(result: dict[str, Any]) -> EncodedImagePayload:
    required = {
        "image_bytes",
        "media_type",
        "filename",
        "image_width",
        "image_height",
        "image_mode",
        "encode_elapsed",
    }
    missing = required.difference(result)
    if missing:
        raise SkiaCardBoxRenderError(f"native renderer returned incomplete payload: {sorted(missing)}")
    image_bytes = result["image_bytes"]
    if not isinstance(image_bytes, bytes):
        raise SkiaCardBoxRenderError("native renderer image_bytes must be bytes")
    return EncodedImagePayload(
        image_bytes=image_bytes,
        media_type=str(result["media_type"]),
        filename=str(result["filename"]),
        image_width=int(result["image_width"]),
        image_height=int(result["image_height"]),
        image_mode=str(result["image_mode"]),
        encode_elapsed=float(result["encode_elapsed"]),
    )


def _cache_key(rqd: CardBoxRequest) -> str:
    return f"{_build_card_box_cache_key(rqd)}|skia|{EXPORT_IMAGE_FORMAT}|{JPG_QUALITY}"


async def render_card_box_payload(rqd: CardBoxRequest) -> EncodedImagePayload:
    cache_key = _cache_key(rqd)
    cached = get_skia_payload_cached(cache_key)
    if cached is not None:
        return cached
    native = _load_native_renderer()
    # The layout is built in Python (Render IR v2); Rust render_scene is a pure interpreter.
    scene = build_card_box_scene(rqd)
    ir_json = json.dumps(scene, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    result = await run_in_pool(native.render_scene, ir_json)
    if not isinstance(result, dict):
        raise SkiaCardBoxRenderError("native renderer must return a dict")
    payload = _payload_from_native(result)
    put_skia_payload_cache(cache_key, payload, len(payload.image_bytes))
    return payload


async def try_render_card_box_payload(rqd: CardBoxRequest) -> EncodedImagePayload | None:
    if not settings.drawing.use_skia_card_box:
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
