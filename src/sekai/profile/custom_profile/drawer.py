from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PIL import Image

from src.sekai.base.utils import run_in_pool
from src.sekai.profile.custom_profile.renderer import PROFILE_RENDER_VIEW_H, PROFILE_RENDER_VIEW_W, PNGRenderer
from src.sekai.profile.model import CustomProfileCardRenderRequest
from src.settings import (
    CUSTOM_PROFILE_ASSETS_DIR,
    CUSTOM_PROFILE_FONTS_DIR,
    CUSTOM_PROFILE_PARALLEL_WORKERS,
    CUSTOM_PROFILE_SHAPE_SPRITE_DIR,
    CUSTOM_PROFILE_TMP_FONT_METADATA,
    CUSTOM_PROFILE_UNITY_UI_SPRITE_DIR,
)

logger = logging.getLogger(__name__)


def _require_path(name: str, path: Path | None, *, is_file: bool = False) -> Path:
    if path is None:
        raise RuntimeError(f"drawing.{name} is not configured")
    if not path.exists():
        raise FileNotFoundError(f"drawing.{name} does not exist: {path}")
    if is_file and not path.is_file():
        raise RuntimeError(f"drawing.{name} must be a file: {path}")
    if not is_file and not path.is_dir():
        raise RuntimeError(f"drawing.{name} must be a directory: {path}")
    return path


def _expand_region_path(path: Path, region: str) -> Path:
    raw = str(path)
    if "{region}" not in raw:
        return path
    return Path(raw.replace("{region}", region))


def _require_region_path(name: str, path: Path | None, region: str, *, is_file: bool = False) -> Path:
    if path is None:
        raise RuntimeError(f"drawing.{name} is not configured")
    return _require_path(name, _expand_region_path(path, region), is_file=is_file)


def _optional_region_file(name: str, path: Path | None, region: str) -> Path | None:
    if path is None:
        return None
    expanded = _expand_region_path(path, region)
    if not expanded.exists():
        logger.warning("optional drawing.%s missing: %s", name, expanded)
        return None
    if not expanded.is_file():
        raise RuntimeError(f"drawing.{name} must be a file: {expanded}")
    return expanded


def _render_custom_profile_card_sync(
    card: dict[str, Any],
    profile_context: dict[str, Any],
    resources: dict[str, Any],
    region: str,
) -> Image.Image:
    assets = _require_region_path("custom_profile_assets_dir", CUSTOM_PROFILE_ASSETS_DIR, region)
    fonts = _require_region_path("custom_profile_fonts_dir", CUSTOM_PROFILE_FONTS_DIR, region)
    tmp_font_metadata = _optional_region_file(
        "custom_profile_tmp_font_metadata",
        CUSTOM_PROFILE_TMP_FONT_METADATA,
        region,
    )
    shape_sprite_dir = _require_region_path(
        "custom_profile_shape_sprite_dir",
        CUSTOM_PROFILE_SHAPE_SPRITE_DIR,
        region,
    )
    unity_ui_sprite_dir = _require_region_path(
        "custom_profile_unity_ui_sprite_dir",
        CUSTOM_PROFILE_UNITY_UI_SPRITE_DIR,
        region,
    )

    renderer = PNGRenderer(
        masterdata=None,
        assets=assets,
        fonts=fonts,
        resources=resources,
        tmp_font_metadata=tmp_font_metadata,
        shape_sprite_dir=shape_sprite_dir,
        profile_context=profile_context,
        parallel_workers=max(1, int(CUSTOM_PROFILE_PARALLEL_WORKERS or 1)),
        parallel_stage="transform",
        clip_canvas_transform=True,
        canvas_w=int(PROFILE_RENDER_VIEW_W),
        canvas_h=int(PROFILE_RENDER_VIEW_H),
        origin_x=PROFILE_RENDER_VIEW_W / 2.0,
        origin_y=PROFILE_RENDER_VIEW_H / 2.0,
        unity_ui_sprite_dir=unity_ui_sprite_dir,
        region=region,
    )
    return renderer.render_card(card)


async def compose_custom_profile_card_image(request: CustomProfileCardRenderRequest) -> Image.Image:
    return await run_in_pool(
        _render_custom_profile_card_sync,
        dict(request.card),
        dict(request.profile_context),
        dict(request.resources),
        request.region,
    )
