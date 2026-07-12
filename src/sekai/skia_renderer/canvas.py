"""Render a built plot.py ``Canvas`` widget tree through the Skia path.

The drawer builds its widget tree as usual; instead of ``canvas.get_img()`` (Pillow), this
draws the same tree into an :class:`IRPainter` to produce a Render IR scene + any runtime
images, then calls the native ``render_scene``. Any unsupported op or error returns ``None``
so the caller falls back to the Pillow composer. See ``docs/skia-pillow-coverage-gaps.md``.
"""

from __future__ import annotations

from datetime import datetime
import importlib
import json
import logging
import os
from typing import Any

from src.core.heavy_render_pool import EncodedImagePayload
from src.sekai.base.utils import run_in_pool
from src.sekai.skia_renderer.ir_painter import IRPainter, SkiaUnsupported
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


def load_native_renderer():
    """Import the native Skia renderer module (shared by the shim and card paths)."""
    return importlib.import_module("haruki_skia_renderer")


_REQUIRED = {
    "image_bytes",
    "media_type",
    "filename",
    "image_width",
    "image_height",
    "image_mode",
    "encode_elapsed",
}


def background_hour() -> float:
    override = os.getenv("HARUKI_BG_TEST_HOUR")
    if override is not None:
        try:
            return max(0.0, min(23.999, float(override)))
        except ValueError:
            pass
    now = datetime.now()
    return now.hour + now.minute / 60 + now.second / 3600


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
    )


async def render_canvas_payload(
    canvas, *, bg_hour: float | None = None, scale: float | None = None, export_format: str | None = None
) -> EncodedImagePayload | None:
    """Render a built Canvas via IRPainter → Skia, or return None to fall back to Pillow.

    ``scale`` mirrors ``Canvas.get_img(scale)`` (render at 1x, resize the final raster).
    ``export_format`` overrides the global export format for endpoints that pin one
    (mirrors ``image_to_response(..., export_format=...)``).
    """
    if not settings.drawing.use_skia_plot:
        return None
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
        # Run ALL the CPU work — layout draw, IR build, JSON encode, mem-image capture, and
        # native render — in one pool task so it parallelizes under concurrency (the native
        # render releases the GIL). Doing the draw/json/encode on the event-loop thread would
        # serialize it across requests and cap throughput.
        size = canvas._get_self_size()
        painter = IRPainter(
            size,
            assets_base_dir=str(ASSETS_BASE_DIR),
            font_dir=str(FONT_DIR),
            default_font=DEFAULT_FONT,
            bold_font=DEFAULT_BOLD_FONT,
            heavy_font=DEFAULT_HEAVY_FONT,
            emoji_font=DEFAULT_EMOJI_FONT,
            bg_hour=bg,
            export_format=eff_format,
            jpg_quality=JPG_QUALITY,
        )
        canvas.draw(painter)
        scene, mem_images = painter.build_scene()
        if eff_scale is not None:
            scene["scale"] = eff_scale
        ir_json = json.dumps(scene, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return native.render_scene(ir_json, mem_images)

    try:
        result = await run_in_pool(_render)
        return payload_from_native(result)
    except SkiaUnsupported as exc:
        logger.info("plot canvas not Skia-expressible (%s); falling back to Pillow", exc)
        return None
    except Exception:
        logger.exception("Skia canvas render failed; falling back to Pillow")
        return None
