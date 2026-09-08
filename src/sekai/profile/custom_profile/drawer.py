from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PIL import Image

from src.sekai.base.utils import run_in_pool
from src.sekai.profile.custom_profile.limits import validate_custom_profile_card
from src.sekai.profile.custom_profile.renderer import PROFILE_RENDER_VIEW_H, PROFILE_RENDER_VIEW_W, PNGRenderer
from src.sekai.profile.model import CustomProfileCardRenderRequest
from src.settings import (
    CUSTOM_PROFILE_ASSETS_DIR,
    CUSTOM_PROFILE_FONTS_DIR,
    CUSTOM_PROFILE_MAX_ELEMENTS,
    CUSTOM_PROFILE_MAX_LAYER_PIXELS,
    CUSTOM_PROFILE_MAX_SCALE,
    CUSTOM_PROFILE_MAX_SCENE_BYTES,
    CUSTOM_PROFILE_MAX_TEXT_LENGTH,
    CUSTOM_PROFILE_MAX_TEXT_SIZE,
    CUSTOM_PROFILE_PARALLEL_WORKERS,
    CUSTOM_PROFILE_SHAPE_SPRITE_DIR,
    CUSTOM_PROFILE_TMP_FONT_METADATA,
    CUSTOM_PROFILE_UNITY_UI_SPRITE_DIR,
)

from .resource_paths import (
    REGION_CODES as REGION_CODES,
    _expand_region_path as _expand_region_path,
    _optional_region_file,
    _region_path_candidates as _region_path_candidates,
    _require_path as _require_path,
    _require_region_path,
)

logger = logging.getLogger(__name__)


def _render_custom_profile_card_sync(
    card: dict[str, Any],
    profile_context: dict[str, Any],
    resources: dict[str, Any],
    region: str,
) -> Image.Image:
    validate_custom_profile_card(
        card,
        max_elements=CUSTOM_PROFILE_MAX_ELEMENTS,
        max_scale=CUSTOM_PROFILE_MAX_SCALE,
        max_text_size=CUSTOM_PROFILE_MAX_TEXT_SIZE,
        max_text_length=CUSTOM_PROFILE_MAX_TEXT_LENGTH,
    )
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
        max_layer_pixels=CUSTOM_PROFILE_MAX_LAYER_PIXELS,
        max_scene_bytes=CUSTOM_PROFILE_MAX_SCENE_BYTES,
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
