"""Skia Render-IR path for the /honor endpoint (shared badge tree + chart-style watermark shell).

The badge itself is NOT built here: it is the same ``HonorBadgeBox`` widget tree
(``honor.widget``) that the Pillow path renders, drawn into an ``IRPainter`` by
``skia_renderer.canvas.build_canvas_ir``. This module only owns the shell that the widget tree
cannot express — the raster watermark footer the route would otherwise add AFTER the compose
(``add_request_watermark_to_image``: a stretched copy of the image's own bottom rows plus two
shadowed text lines). That footer samples the rendered canvas, so it is a ``SelfImage`` node,
and is drawn for each response. The static badge may come from a bounded native fragment
cache; its source still comes exclusively from the shared asset-backed widget tree.

The badge is lowered as a reusable asset-backed ``NativeSubtree``. Bonds character sprites keep
their legacy full-resize-then-destination-clip order through rectangular Group clips around full
Image nodes; Python neither decodes/crops them nor ships ``mem:`` rasters. Subtree lowering is
required to be asset-backed before anything is spliced into the watermark scene.

Any unsupported shape or unreadable *required* asset returns ``None`` so the service rejects
the incomplete native render.
"""

from __future__ import annotations

import json
import logging
import time

from src.core.debug import set_render_backend
from src.core.image_payload import EncodedImagePayload
from src.sekai.base.draw import (
    WATERMARK_BOTTOM_OFFSET,
    WATERMARK_LINE_SEP,
    WATERMARK_RIGHT_OFFSET,
    WATERMARK_SHADOW_OFFSET,
    WATERMARK_TOP_OFFSET,
    build_request_watermark_text,
    get_watermark_render_spec,
)
from src.sekai.base.font_metrics import get_layout_font as get_font
from src.sekai.base.image_source import native_image_memory
from src.sekai.base.text_layout import get_text_size
from src.sekai.base.utils import build_rendered_image_cache_key, run_in_pool
from src.sekai.skia_renderer.canvas import load_native_renderer, payload_from_native, skia_plot_enabled
from src.sekai.skia_renderer.fragment_cache import get_native_fragment_cached, render_cached_native_fragment
from src.sekai.skia_renderer.ir_builder import IRBuilder
from src.sekai.skia_renderer.payload_cache import get_skia_payload_cached, put_skia_payload_cache
from src.sekai.skia_renderer.render_stats import (
    OUTCOME_CACHE_HIT,
    OUTCOME_DISABLED,
    OUTCOME_ERROR,
    OUTCOME_FALLBACK,
    OUTCOME_SKIA,
    backend_for_outcome,
    record_native_metrics,
    record_render,
)
from src.sekai.skia_renderer.subtree import lower_canvas_subtree
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT, EXPORT_IMAGE_FORMAT, FONT_DIR, JPG_QUALITY

from .model import HonorRequest

logger = logging.getLogger("honor.draw.perf")

# /render-stats + the ``backend=`` log field key on this name (the route is /api/pjsk/honor, and
# every parity payload variant — honor/honor_bonds/honor_birthday/honor_fcap — is this one
# endpoint). Like the chart path, this module wraps the shared tree in a two-layer watermark
# shell, so it never goes through render_canvas_payload and has to record its own outcome —
# exactly one per render attempt, mirroring the canvas helper.
HONOR_ENDPOINT = "honor"


def _record(outcome: str, payload: EncodedImagePayload | None = None) -> None:
    """Record one render attempt for /render-stats and tag the request context.

    Mirrors ``src.sekai.skia_renderer.canvas._record`` (and ``chart.drawer._record``): this path
    cannot reuse the canvas helper, so it records through the same public primitives instead.
    Folding in ``native_metrics`` is what keeps a broken font config visible — otherwise
    /render-stats would report 0 font fallbacks while every honor badge rendered in sans-serif.
    """
    record_render(HONOR_ENDPOINT, outcome)
    backend = backend_for_outcome(outcome)
    set_render_backend(backend)
    if payload is not None:
        payload.backend = backend
        record_native_metrics(payload.native_metrics)


def _new_builder(width: int, height: int, export_format: str = "png") -> IRBuilder:
    return IRBuilder(
        width,
        height,
        assets_base_dir=str(ASSETS_BASE_DIR),
        font_dir=str(FONT_DIR),
        default_font=DEFAULT_FONT,
        bold_font=DEFAULT_BOLD_FONT,
        export_format=export_format,
        jpg_quality=JPG_QUALITY,
    )


async def try_render_full_honor_payload(rqd: HonorRequest) -> EncodedImagePayload | None:
    """Skia path for the /honor route: the shared badge tree + the route's raster watermark
    footer (``add_request_watermark_to_image`` equivalent), with reusable native badge pixels.
    Returns ``None`` (gate off / unsupported / failure) so the service can reject the declined native render.
    """
    if not skia_plot_enabled():
        _record(OUTCOME_DISABLED)
        return None
    try:
        native = load_native_renderer()
    except ImportError as exc:
        logger.error("haruki_skia_renderer not importable (%s); declining native render", exc)
        _record(OUTCOME_FALLBACK)
        return None

    # lazy: the drawer re-exports this module's entry point at its end
    from src.sekai.honor.drawer import build_full_honor_cache_key, build_honor_badge_cache_key, load_honor_images
    from src.sekai.honor.widget import build_honor_badge_canvas

    # The cached payload embeds the footer, so the key must cover everything the footer text
    # derives from (dt/timezone) on top of the Pillow composed key (which excludes timezone).
    watermark_text = build_request_watermark_text(rqd)
    cache_key = f"{build_full_honor_cache_key(rqd)}|skia|{EXPORT_IMAGE_FORMAT}|{JPG_QUALITY}|wm:{watermark_text}"
    cached = get_skia_payload_cached(cache_key)
    if cached is not None:
        _record(OUTCOME_CACHE_HIT, cached)
        return cached

    try:
        badge_key = build_rendered_image_cache_key(
            "honor.native_badge",
            build_honor_badge_cache_key(rqd),
            extra=[
                str(ASSETS_BASE_DIR),
                str(FONT_DIR),
                DEFAULT_FONT,
                DEFAULT_BOLD_FONT,
                EXPORT_IMAGE_FORMAT,
                JPG_QUALITY,
            ],
        )
        cached_badge = get_native_fragment_cached(badge_key)
        images = await load_honor_images(rqd) if cached_badge is None else None
    except Exception:
        # Record all asset failures before declining the incomplete native response.
        logger.info("honor assets not loadable for the Skia path; declining native render", exc_info=True)
        _record(OUTCOME_FALLBACK)
        return None

    def _render():
        fragment = cached_badge
        badge = None
        if fragment is None:
            canvas = build_honor_badge_canvas(rqd, images)
            if canvas is None:
                return None
            badge = lower_canvas_subtree(canvas, require_asset_backed=True, export_format=EXPORT_IMAGE_FORMAT)
            fragment = render_cached_native_fragment(badge, "honor.badge", lookup_key=badge_key)
        w, h = fragment.size if fragment is not None else badge.size
        mem_images: dict[str, object] = {}

        # Single pass: badge nodes + stretched bottom-strip footer (a SelfImage snapshot of
        # the badge rows just rendered above it) + shadowed watermark lines (mirrors
        # add_watermark_to_image; same spec as the chart watermark shell).
        font_size, lines, text_w, text_h = get_watermark_render_spec(watermark_text, w - WATERMARK_RIGHT_OFFSET, 12)
        footer_h = WATERMARK_TOP_OFFSET + text_h + WATERMARK_BOTTOM_OFFSET + WATERMARK_SHADOW_OFFSET
        b = _new_builder(w, h + footer_h, export_format=EXPORT_IMAGE_FORMAT)
        # Clip to the badge rect: the badge canvas is (w, h), so anything the widget draws
        # outside it (the bonds chara icons overhang) must be cropped exactly as the Pillow
        # canvas bounds crop it.
        with b.group((0, 0), (w, h), clip={"kind": "rect"}):
            if fragment is None:
                badge.splice_into(
                    b,
                    mem_images,
                    namespace="honor.badge",
                    require_asset_backed=True,
                )
            else:
                mem_images["honor.badge.cached"] = native_image_memory(fragment)
                b.image("mem:honor.badge.cached", (0, 0), (w, h), blend="src", sampling="nearest")
        sample_h = max(1, min(h, footer_h))
        b.self_image((0, h), (w, footer_h), source_rect=(0, h - sample_h, w, h))
        font = get_font(DEFAULT_FONT, font_size)
        x = max(0, w - text_w - WATERMARK_RIGHT_OFFSET)
        y = h + WATERMARK_TOP_OFFSET
        for idx, line in enumerate(lines):
            line_w = get_text_size(font, line)[0]
            lx = x + max(0, text_w - line_w)
            ly = y + idx * (font_size + WATERMARK_LINE_SEP)
            # PIL ImageDraw.text default anchor is left/top-of-ascent -> IR "ascender" baseline.
            b.text(line, (lx + 1, ly + 1), "default", font_size, baseline="ascender", fill=(75, 75, 75, 255))
            b.text(line, (lx, ly), "default", font_size, baseline="ascender", fill=(255, 255, 255, 255))
        ir_json = json.dumps(b.build(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        # The only memory image allowed here is an immutable native badge fragment.
        # The source subtree still must be asset-backed; Pillow rasters are never accepted.
        return native.render_scene(ir_json, mem_images)

    started = time.perf_counter()
    try:
        result = await run_in_pool(_render)
        if result is None:
            # The request is not renderable at all (the Pillow composer would return None too):
            # a fallback, not an error.
            _record(OUTCOME_FALLBACK)
            return None
        payload = payload_from_native(result)
    except Exception:
        logger.exception("honor backend=skia failed; declining native render")
        _record(OUTCOME_ERROR)
        return None
    _record(OUTCOME_SKIA, payload)
    logger.info(
        "honor backend=skia total=%.3fs bytes=%d image=%sx%s",
        time.perf_counter() - started,
        len(payload.image_bytes),
        payload.image_width,
        payload.image_height,
    )
    put_skia_payload_cache(cache_key, payload, len(payload.image_bytes))
    return payload
