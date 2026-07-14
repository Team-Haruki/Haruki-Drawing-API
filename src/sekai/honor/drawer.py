"""Asset loading + caching for the honor badge. The LAYOUT lives in ``widget.py``.

``compose_full_honor_image`` (async, Pillow) and the Skia route
(``skia.try_render_full_honor_payload``) both build the SAME ``HonorBadgeBox`` widget tree from
the same loaded images; this module only resolves the request's assets, keys the composed-image
cache, and renders the tree with Pillow.
"""

import asyncio
import logging

from PIL import Image

from src.sekai.base.utils import (
    build_rendered_image_cache_key,
    get_composed_image_cached,
    get_image_asset_signature,
    get_img_from_path,
    put_composed_image_cache,
    run_in_pool,
)
from src.settings import ASSETS_BASE_DIR

# 从 model.py 导入数据模型
from .model import HonorRequest
from .widget import (
    build_honor_badge_canvas,
    # re-exported because custom_profile/renderer.py imports it from here.
    honor_group_uses_scroll_level as honor_group_uses_scroll_level,
)

# NOTE deliberately NOT re-exported: is_world_link_rank_style / resolve_event_rank_position. They
# have no importers, and resolve_event_rank_position's signature changed in the widget-tree port
# (PIL images -> size tuples). Keeping the old public name pointing at new semantics is how you
# break an out-of-tree caller silently; import them from .widget if you ever need them.

logger = logging.getLogger(__name__)


def compose_full_honor_image_from_loaded_assets(
    rqd: HonorRequest,
    images: dict[str, Image.Image | None],
) -> Image.Image | None:
    """Synchronous compose from already-decoded images (the custom-profile renderer's path).

    Renders the shared widget tree with ``Canvas.get_img_sync`` — same tree, same ops, same
    pixels as the async entry point below."""
    canvas = build_honor_badge_canvas(rqd, images)
    if canvas is None:
        return None
    return canvas.get_img_sync()


async def load_honor_images(rqd: HonorRequest) -> dict[str, Image.Image | None]:
    """Decode every asset the request's branch needs, concurrently.

    Required assets raise (the caller surfaces the canonical error); only ``rank_img`` is
    optional, and a missing one is logged and skipped, exactly as before."""

    async def load_honor_image(path: str | None):
        return await get_img_from_path(ASSETS_BASE_DIR, path, on_missing="raise")

    async def load_optional_image(path: str | None):
        if not path:
            return None
        try:
            return await load_honor_image(path)
        except (FileNotFoundError, OSError, ValueError):
            logger.warning("optional honor asset missing: %s", path)
            return None

    tasks: dict[str, object] = {}

    if rqd.is_empty and rqd.empty_honor_path:
        tasks["empty_honor"] = load_honor_image(rqd.empty_honor_path)
    if rqd.lv_img_path:
        tasks["lv_img"] = load_honor_image(rqd.lv_img_path)
    if rqd.lv6_img_path:
        tasks["lv6_img"] = load_honor_image(rqd.lv6_img_path)
    if rqd.frame_img_path:
        tasks["frame_img"] = load_honor_image(rqd.frame_img_path)

    htype = rqd.honor_type
    gtype = rqd.group_type
    if htype == "birthday" and rqd.frame_degree_level_img_path:
        tasks["frame_degree_level_img"] = load_honor_image(rqd.frame_degree_level_img_path)

    if htype in ("normal", "birthday"):
        if rqd.honor_img_path:
            tasks["honor_img"] = load_honor_image(rqd.honor_img_path)
        if rqd.rank_img_path:
            tasks["rank_img"] = load_optional_image(rqd.rank_img_path)
        if honor_group_uses_scroll_level(gtype) and rqd.scroll_img_path:
            tasks["scroll_img"] = load_honor_image(rqd.scroll_img_path)
    elif htype == "bonds":
        if rqd.bonds_bg_path:
            tasks["bonds_bg"] = load_honor_image(rqd.bonds_bg_path)
        if rqd.bonds_bg_path2:
            tasks["bonds_bg2"] = load_honor_image(rqd.bonds_bg_path2)
        if rqd.chara_icon_path:
            tasks["chara_icon_1"] = load_honor_image(rqd.chara_icon_path)
        if rqd.chara_icon_path2:
            tasks["chara_icon_2"] = load_honor_image(rqd.chara_icon_path2)
        if rqd.mask_img_path:
            tasks["mask_img"] = load_honor_image(rqd.mask_img_path)
        if rqd.word_img_path:
            tasks["word_img"] = load_honor_image(rqd.word_img_path)

    keys = list(tasks.keys())
    values = await asyncio.gather(*tasks.values()) if tasks else []
    return dict(zip(keys, values))


def _build_full_honor_cache_key(rqd: HonorRequest) -> str:
    request_payload = rqd.model_dump(mode="json", exclude_none=False, exclude={"timezone"})
    asset_signatures = {
        "honor_img": get_image_asset_signature(ASSETS_BASE_DIR, rqd.honor_img_path),
        "rank_img": get_image_asset_signature(ASSETS_BASE_DIR, rqd.rank_img_path),
        "lv_img": get_image_asset_signature(ASSETS_BASE_DIR, rqd.lv_img_path),
        "lv6_img": get_image_asset_signature(ASSETS_BASE_DIR, rqd.lv6_img_path),
        "empty_honor": get_image_asset_signature(ASSETS_BASE_DIR, rqd.empty_honor_path),
        "scroll_img": get_image_asset_signature(ASSETS_BASE_DIR, rqd.scroll_img_path),
        "word_img": get_image_asset_signature(ASSETS_BASE_DIR, rqd.word_img_path),
        "chara_icon_1": get_image_asset_signature(ASSETS_BASE_DIR, rqd.chara_icon_path),
        "chara_icon_2": get_image_asset_signature(ASSETS_BASE_DIR, rqd.chara_icon_path2),
        "bonds_bg": get_image_asset_signature(ASSETS_BASE_DIR, rqd.bonds_bg_path),
        "bonds_bg2": get_image_asset_signature(ASSETS_BASE_DIR, rqd.bonds_bg_path2),
        "mask_img": get_image_asset_signature(ASSETS_BASE_DIR, rqd.mask_img_path),
        "frame_img": get_image_asset_signature(ASSETS_BASE_DIR, rqd.frame_img_path),
        "frame_degree_level_img": get_image_asset_signature(ASSETS_BASE_DIR, rqd.frame_degree_level_img_path),
    }
    return build_rendered_image_cache_key(
        "full_honor_image",
        request_payload,
        asset_signatures=asset_signatures,
    )


async def compose_full_honor_image(rqd: HonorRequest):
    cache_key = _build_full_honor_cache_key(rqd)
    cached = get_composed_image_cached(cache_key)
    if cached is not None:
        return cached

    logger.info(
        "compose honor debug: type=%s group=%s main=%s level=%s rarity=%s "
        "honor_img=%s frame=%s frame_level=%s rank=%s scroll=%s word=%s "
        "bonds_bg=%s bonds_bg2=%s mask=%s lv_img=%s lv6_img=%s",
        rqd.honor_type,
        rqd.group_type,
        rqd.is_main_honor,
        rqd.honor_level,
        rqd.honor_rarity,
        rqd.honor_img_path,
        rqd.frame_img_path,
        rqd.frame_degree_level_img_path,
        rqd.rank_img_path,
        rqd.scroll_img_path,
        rqd.word_img_path,
        rqd.bonds_bg_path,
        rqd.bonds_bg_path2,
        rqd.mask_img_path,
        rqd.lv_img_path,
        rqd.lv6_img_path,
    )

    images = await load_honor_images(rqd)
    canvas = build_honor_badge_canvas(rqd, images)
    if canvas is None:
        return None
    # The widget tree draws in a pool thread (Canvas.get_img -> Painter.get -> run_in_pool);
    # building it is pure layout bookkeeping.
    composed = await run_in_pool(canvas.get_img_sync)
    if composed is not None:
        put_composed_image_cache(cache_key, composed)
    return composed


# Skia shadow path (skia.py) re-exported so the route and the parity harness resolve it from
# the drawer namespace; kept in its own module so this file stays the Pillow entry point.
from .skia import try_render_full_honor_payload as try_render_full_honor_payload
