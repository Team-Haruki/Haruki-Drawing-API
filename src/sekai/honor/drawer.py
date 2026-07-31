"""Asset loading + caching for the honor badge. The LAYOUT lives in ``widget.py``.

``compose_full_honor_image`` (async, Pillow) and the Skia route
(``skia.try_render_full_honor_payload``) both build the SAME ``HonorBadgeBox`` widget tree from
the same loaded images; this module only resolves the request's assets, keys the composed-image
cache, and renders the tree with Pillow.
"""

import asyncio
import logging

from src.sekai.base.utils import (
    ImageSource,
    build_rendered_image_cache_key,
    get_asset_image_ref,
    get_composed_image_cached,
    get_image_asset_signature,
    put_composed_image_cache,
    run_in_pool,
)
from src.settings import ASSETS_BASE_DIR

# 从 model.py 导入数据模型
from .assets import HONOR_ASSET_MANIFEST, honor_asset_specs
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
    images: dict[str, ImageSource | None],
):
    """Synchronous compose from already-resolved sources (the custom-profile renderer's path).

    Sources may be decoded images or lazy asset references. Renders the shared widget tree with
    ``Canvas.get_img_sync`` — same tree, same ops, same pixels as the async entry point below."""
    canvas = build_honor_badge_canvas(rqd, images)
    if canvas is None:
        return None
    return canvas.get_img_sync()


async def load_honor_images(rqd: HonorRequest) -> dict[str, ImageSource | None]:
    """Resolve every asset the request's branch needs, concurrently, without decoding pixels.

    Required assets raise (the caller surfaces the canonical error); only ``rank_img`` is
    optional, and a missing one is logged and skipped, exactly as before. Pillow resolves these
    lazy refs inside its render worker; IRPainter keeps them as asset paths for Rust."""

    async def load_honor_image(path: str | None):
        return await get_asset_image_ref(ASSETS_BASE_DIR, path, on_missing="raise")

    async def load_optional_image(path: str | None):
        if not path:
            return None
        try:
            return await load_honor_image(path)
        except (FileNotFoundError, OSError, ValueError):
            logger.warning("optional honor asset missing: %s", path)
            return None

    tasks: dict[str, object] = {}
    for spec in honor_asset_specs(rqd):
        raw_path = getattr(rqd, spec.path_field)
        if not raw_path:
            continue
        loader = load_optional_image if spec.on_supplied_missing == "ignore" else load_honor_image
        tasks[spec.image_key] = loader(raw_path)

    keys = list(tasks.keys())
    values = await asyncio.gather(*tasks.values()) if tasks else []
    return dict(zip(keys, values))


def build_full_honor_cache_key(rqd: HonorRequest) -> str:
    request_payload = rqd.model_dump(mode="json", exclude_none=False, exclude={"timezone"})
    asset_signatures = {
        image_key: get_image_asset_signature(ASSETS_BASE_DIR, getattr(rqd, path_field))
        for image_key, path_field in HONOR_ASSET_MANIFEST.items()
    }
    return build_rendered_image_cache_key(
        "full_honor_image",
        request_payload,
        asset_signatures=asset_signatures,
    )


async def build_honor_badge_canvas_from_request(rqd: HonorRequest):
    """Resolve one request to the shared Honor badge canvas without rasterizing it."""

    return build_honor_badge_canvas(rqd, await load_honor_images(rqd))


async def compose_full_honor_image(rqd: HonorRequest):
    cache_key = build_full_honor_cache_key(rqd)
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

    canvas = await build_honor_badge_canvas_from_request(rqd)
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
