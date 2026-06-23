from __future__ import annotations

from datetime import datetime
import importlib
import json
import logging
import os
from pathlib import PurePosixPath, PureWindowsPath
import time
from typing import Any

from src.core.heavy_render_pool import EncodedImagePayload
from src.sekai.base.timezone import request_now
from src.sekai.base.utils import run_in_pool
from src.sekai.card.model import CardListRequest
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
    ir_json = json.dumps(build_card_list_ir(rqd), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    result = await run_in_pool(native.render_card_list, ir_json)
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
