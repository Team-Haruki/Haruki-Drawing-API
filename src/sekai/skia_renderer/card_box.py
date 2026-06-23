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
from src.sekai.base import CHARACTER_COLOR_CODE
from src.sekai.base.utils import run_in_pool
from src.sekai.card.model import CardBoxRequest
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


async def render_card_box_payload(rqd: CardBoxRequest) -> EncodedImagePayload:
    native = _load_native_renderer()
    ir_json = json.dumps(build_card_box_ir(rqd), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    result = await run_in_pool(native.render_card_box, ir_json)
    if not isinstance(result, dict):
        raise SkiaCardBoxRenderError("native renderer must return a dict")
    return _payload_from_native(result)


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
