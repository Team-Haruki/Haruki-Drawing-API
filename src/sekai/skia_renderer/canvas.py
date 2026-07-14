"""Render a built plot.py ``Canvas`` widget tree through the Skia path.

The drawer builds its widget tree as usual; instead of ``canvas.get_img()`` (Pillow), this
draws the same tree into an :class:`IRPainter` to produce a Render IR scene + any runtime
images, then calls the native ``render_scene``. Any unsupported op or error returns ``None``
so the caller falls back to the Pillow composer. See ``docs/skia-pillow-coverage-gaps.md``.
"""

from __future__ import annotations

import importlib
import json
import logging
from typing import Any

from src.core.debug import set_render_backend
from src.core.heavy_render_pool import EncodedImagePayload
from src.sekai.base.triangle_bg import background_hour
from src.sekai.base.utils import run_in_pool
from src.sekai.skia_renderer.ir_builder import IRBuilder
from src.sekai.skia_renderer.ir_painter import IRPainter, SkiaUnsupported
from src.sekai.skia_renderer.render_stats import (
    OUTCOME_DISABLED,
    OUTCOME_ERROR,
    OUTCOME_FALLBACK,
    OUTCOME_SKIA,
    backend_for_outcome,
    record_native_metrics,
    record_render,
)
from src.settings import (
    ASSETS_BASE_DIR,
    DEFAULT_BOLD_FONT,
    DEFAULT_EMOJI_FONT,
    DEFAULT_FONT,
    DEFAULT_HEAVY_FONT,
    EXPORT_IMAGE_FORMAT,
    FONT_DIR,
    JPG_QUALITY,
    settings,
)

logger = logging.getLogger("plot.draw.perf")


def skia_plot_enabled() -> bool:
    return bool(settings.drawing.use_skia_plot)


# Minimum IR capability this code emits (5 = capability 4 + SelfImage canvas snapshot).
# An older wheel would silently drop features, so refuse it and fail open to Pillow.
REQUIRED_NATIVE_IR_CAPABILITY = 7


def load_native_renderer():
    """Import the native Skia renderer module (shared by the shim and card paths).

    Raises ImportError when the module is missing OR too old for the IR this code
    builds — callers already treat ImportError as the fail-open path.
    """
    native = importlib.import_module("haruki_skia_renderer")
    capability = getattr(native, "IR_CAPABILITY", 0)
    if capability < REQUIRED_NATIVE_IR_CAPABILITY:
        raise ImportError(
            f"haruki_skia_renderer IR capability {capability} < required "
            f"{REQUIRED_NATIVE_IR_CAPABILITY}; rebuild/upgrade the wheel"
        )
    return native


_REQUIRED = {
    "image_bytes",
    "media_type",
    "filename",
    "image_width",
    "image_height",
    "image_mode",
    "encode_elapsed",
}


def payload_from_native(result: dict[str, Any]) -> EncodedImagePayload:
    if not isinstance(result, dict) or _REQUIRED.difference(result):
        raise ValueError("native renderer returned an incomplete payload")
    image_bytes = result["image_bytes"]
    if not isinstance(image_bytes, bytes):
        raise ValueError("native renderer image_bytes must be bytes")
    return EncodedImagePayload(
        image_bytes=image_bytes,
        media_type=str(result["media_type"]),
        filename=str(result["filename"]),
        image_width=int(result["image_width"]),
        image_height=int(result["image_height"]),
        image_mode=str(result["image_mode"]),
        encode_elapsed=float(result["encode_elapsed"]),
        native_metrics=dict(result["native_metrics"]) if isinstance(result.get("native_metrics"), dict) else None,
    )


# A DoS guard, NOT a mirror of Pillow's CANVAS_SIZE_LIMIT (4096x4096 = 16.8 Mpx).
#
# Mirroring the Pillow budget here would be a regression: Skia is the only backend that can
# render a canvas Pillow refuses, and real traffic already gets close — the biggest of the 63
# parity payloads (chart) is 5248x2704 = 14.2 Mpx, i.e. 85% of the Pillow budget. Bouncing
# such a canvas to Pillow just trades a working native render for the Pillow assertion (a 500).
# So this bound only catches the absurd: 64 Mpx is ~4.5x the largest real render and costs
# ~256 MiB for the N32 surface alone.
SKIA_MAX_CANVAS_PIXELS = 64_000_000
SKIA_MAX_CANVAS_EDGE = 32_767


def canvas_size_within_limit(size: tuple[int, int]) -> bool:
    """Whether Skia should attempt this canvas at all (see ``SKIA_MAX_CANVAS_PIXELS``)."""
    w, h = int(size[0]), int(size[1])
    if w <= 0 or h <= 0:
        return False
    if w > SKIA_MAX_CANVAS_EDGE or h > SKIA_MAX_CANVAS_EDGE:
        return False
    return w * h <= SKIA_MAX_CANVAS_PIXELS


def build_canvas_ir(
    canvas,
    *,
    bg_hour: float | None = None,
    export_format: str | None = None,
) -> tuple[IRBuilder, dict[str, Any]]:
    """Draw a built Canvas into an :class:`IRPainter` and hand back its scene builder.

    For callers that need the widget tree's IR as a *sub-scene* rather than a finished render —
    the /honor route splices the badge into the builder that also carries its raster watermark
    footer (a ``SelfImage`` node the widget tree cannot express). Merge with
    ``IRBuilder.splice_root_children`` and pass the returned mem images to
    ``native.render_scene`` alongside the caller's own.

    Synchronous and CPU-bound (it measures the tree and draws it): call it from a pool task.
    Raises ``SkiaUnsupported`` for a tree/size the IR cannot express.
    """
    size = canvas._get_self_size()
    if not canvas_size_within_limit(size):
        raise SkiaUnsupported(f"canvas {size[0]}x{size[1]} exceeds the Skia size guard")
    painter = IRPainter(
        size,
        assets_base_dir=str(ASSETS_BASE_DIR),
        font_dir=str(FONT_DIR),
        default_font=DEFAULT_FONT,
        bold_font=DEFAULT_BOLD_FONT,
        heavy_font=DEFAULT_HEAVY_FONT,
        emoji_font=DEFAULT_EMOJI_FONT,
        bg_hour=background_hour() if bg_hour is None else bg_hour,
        export_format=EXPORT_IMAGE_FORMAT if export_format is None else export_format,
        jpg_quality=JPG_QUALITY,
    )
    canvas.draw(painter)
    painter.assert_balanced()
    return painter.builder, painter.mem_images


async def render_canvas_payload(
    canvas,
    *,
    endpoint: str | None = None,
    bg_hour: float | None = None,
    scale: float | None = None,
    export_format: str | None = None,
) -> EncodedImagePayload | None:
    """Render a built Canvas via IRPainter → Skia, or return None to fall back to Pillow.

    ``scale`` mirrors ``Canvas.get_img(scale)`` (render at 1x, resize the final raster).
    ``export_format`` overrides the global export format for endpoints that pin one
    (mirrors ``image_to_response(..., export_format=...)``).

    ``endpoint`` names the caller for /render-stats and the ``backend=`` log field. It is
    optional only so that an un-wired caller still renders; pass it.

    This is where every render outcome is recorded, so /render-stats and the ``backend=`` log
    field cover every drawing endpoint. **honor** is the one endpoint that still keeps a payload
    cache; it hand-builds its IR anyway and records through its own ``_record`` helper, so it never
    reaches here. (card/box and card/list used to cache here too — their page caches were removed
    because the page bakes in the wall clock.)
    """
    name = endpoint or "unknown"
    if not settings.drawing.use_skia_plot:
        _record(name, OUTCOME_DISABLED)
        return None
    try:
        payload = await _render_canvas_uncounted(canvas, bg_hour=bg_hour, scale=scale, export_format=export_format)
    except SkiaUnsupported as exc:
        logger.info("plot canvas not Skia-expressible (%s); falling back to Pillow", exc)
        _record(name, OUTCOME_FALLBACK)
        return None
    except Exception:
        logger.exception("Skia canvas render failed; falling back to Pillow")
        _record(name, OUTCOME_ERROR)
        return None
    if payload is None:  # native extension unavailable
        _record(name, OUTCOME_FALLBACK)
        return None
    _record(name, OUTCOME_SKIA, payload)
    return payload


async def _render_canvas_uncounted(
    canvas, *, bg_hour: float | None = None, scale: float | None = None, export_format: str | None = None
) -> EncodedImagePayload | None:
    """The actual render. Returns None when the native extension is unavailable, raises
    ``SkiaUnsupported`` when the tree (or its size) is not Skia-expressible. Counting and the
    fail-open catch-all live in :func:`render_canvas_payload`."""
    try:
        native = load_native_renderer()
    except ImportError as exc:
        # Fail-open: a missing/broken native extension must degrade to Pillow, not 500.
        logger.error("haruki_skia_renderer not importable (%s); falling back to Pillow", exc)
        return None
    bg = background_hour() if bg_hour is None else bg_hour
    eff_scale = float(scale) if (scale is not None and abs(scale - 1.0) > 1e-3) else None
    eff_format = EXPORT_IMAGE_FORMAT if export_format is None else export_format

    def _render():
        # Run ALL the CPU work — layout measure, draw, IR build, JSON encode, mem-image capture,
        # and native render — in one pool task so it parallelizes under concurrency (the native
        # render releases the GIL). Doing the measure/draw/json/encode on the event-loop thread
        # would serialize it across requests and cap throughput — which is why the size guard
        # inside build_canvas_ir runs HERE and not before the offload: _get_self_size() walks
        # the whole tree.
        builder, mem_images = build_canvas_ir(canvas, bg_hour=bg, export_format=eff_format)
        scene = builder.build()
        if eff_scale is not None:
            scene["scale"] = eff_scale
        ir_json = json.dumps(scene, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return native.render_scene(ir_json, mem_images)

    return payload_from_native(await run_in_pool(_render))


def _record(endpoint: str, outcome: str, payload: EncodedImagePayload | None = None) -> None:
    """Record the outcome, tag the request context, and stamp the payload with its backend.

    The contextvar is what the ``image.response`` log line reads in this process; the payload
    field is what survives the heavy-worker process boundary (the parent replays it).
    """
    record_render(endpoint, outcome)
    backend = backend_for_outcome(outcome)
    set_render_backend(backend)
    if payload is not None:
        payload.backend = backend
        record_native_metrics(payload.native_metrics)
