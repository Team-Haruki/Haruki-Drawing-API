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
from src.sekai.base.timezone import request_now
from src.sekai.base.utils import run_in_pool
from src.sekai.card.model import CardListRequest
from src.sekai.skia_renderer.card_common import (
    BG_PADDING as _BG_PADDING,
    GRID_PADDING as _GRID_PADDING,
    THUMB as _THUMB,
    TITLE_H as _TITLE_H,
    TITLE_SEP as _TITLE_SEP,
    WATERMARK_FALLBACK as _WATERMARK_FALLBACK,
    center_text as _center_text,
    limited_icon_path as _limited_icon_path,
    notice_title as _notice_title,
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
NON_LIMITED_SUPPLY_TYPES = {"", "normal", "非限定"}


class SkiaCardListRenderError(RuntimeError):
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
    width = math.ceil(_PANEL_WIDTH + _BG_PADDING * 2)
    height = math.ceil(_BG_PADDING * 2 + title_h + grid_h)

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

    watermark = ir["watermark"]
    if watermark["enabled"]:
        text = watermark["text"] or _WATERMARK_FALLBACK
        b.text(text, (width - 150, height - 10), "default", 12, baseline="alphabetic", fill=(0, 0, 0, 120))

    return b.build()


def _load_native_renderer():
    try:
        return importlib.import_module("haruki_skia_renderer")
    except ImportError as exc:
        raise SkiaCardListRenderError("haruki_skia_renderer is not installed") from exc


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
        raise SkiaCardListRenderError(f"native renderer returned incomplete payload: {sorted(missing)}")
    image_bytes = result["image_bytes"]
    if not isinstance(image_bytes, bytes):
        raise SkiaCardListRenderError("native renderer image_bytes must be bytes")
    return EncodedImagePayload(
        image_bytes=image_bytes,
        media_type=str(result["media_type"]),
        filename=str(result["filename"]),
        image_width=int(result["image_width"]),
        image_height=int(result["image_height"]),
        image_mode=str(result["image_mode"]),
        encode_elapsed=float(result["encode_elapsed"]),
    )


async def render_card_list_payload(rqd: CardListRequest) -> EncodedImagePayload:
    native = _load_native_renderer()
    # The layout is built in Python (Render IR v2); Rust render_scene is a pure interpreter.
    scene = build_card_list_scene(rqd)
    ir_json = json.dumps(scene, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    result = await run_in_pool(native.render_scene, ir_json)
    if not isinstance(result, dict):
        raise SkiaCardListRenderError("native renderer must return a dict")
    return _payload_from_native(result)


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
