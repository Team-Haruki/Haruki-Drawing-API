"""
Haruki Drawing API - FastAPI Core Application

This module provides RESTful API endpoints for generating various Sekai images.
All endpoints accept JSON request bodies and return PNG images.

Run with: granian --interface asgi src.core.main:app
Swagger UI: http://localhost:8000/docs
ReDoc: http://localhost:8000/redoc
"""

import asyncio
from contextlib import asynccontextmanager
import logging
import sys

import coloredlogs
from fastapi import FastAPI
from granian import Granian

from src.core import health
from src.core.debug import install_debug_middleware
from src.core.diagnostics import configure_runtime_diagnostics, dump_runtime_diagnostics
from src.core.pjsk import router as pjsk_router
from src.settings import (
    FIELD_STYLE,
    LOG_FORMAT,
    SERVER_HOST,
    SERVER_PORT,
    settings,
)

logger = logging.getLogger(__name__)
_description = """
## 🎨 Haruki Drawing API

This API provides endpoints for generating various Project Sekai images.

### Available Modules:
- **Card**: Generate card detail, list, and box images
- **Costume**: Generate costume list and detail images
- **Music**: Generate music detail, list, progress, and rewards images
- **Profile**: Generate player profile images
- **Event**: Generate event detail, record, and list images
- **VLive**: Generate virtual live reminder-style list images
- **Gacha**: Generate gacha list and detail images
- **Honor**: Generate honor/badge images
- **Score**: Generate score control images
- **Stamp**: Generate stamp list images
- **Education**: Generate challenge live, power bonus, area items, bonds, and leader count images
- **Deck**: Generate deck recommendation images
- **MySekai**: Generate resource, msr map, fixture, gate, music record, and talk list images
- **SK**: Generate ranking lines, history, speed, and prediction images


### Response Format:
All endpoints return PNG images as binary stream.
    """


def _ensure_nogil_runtime() -> None:
    if not hasattr(sys, "_is_gil_enabled"):
        raise RuntimeError("Current Python runtime does not expose GIL status; use CPython 3.14t.")
    if sys._is_gil_enabled():
        raise RuntimeError("GIL is enabled. Start with free-threaded runtime and -X gil=0.")


TMP_CLEANUP_INTERVAL = 300  # 临时文件清理间隔（秒）
DISK_CACHE_CLEANUP_INTERVAL = 3600  # 磁盘缓存清理间隔（秒）


def _check_pillow_fonts() -> list[str]:
    """Names of configured fonts Pillow cannot resolve.

    Probes through ``get_font`` itself rather than re-implementing its path search, so the check
    can never drift from the real lookup: a resolved font is a ``FreeTypeFont``, while a failed
    one silently degrades to PIL's built-in 10px bitmap face.
    """
    from PIL import ImageFont

    from src.sekai.base.painter import get_font
    from src.settings import DEFAULT_BOLD_FONT, DEFAULT_EMOJI_FONT, DEFAULT_FONT, DEFAULT_HEAVY_FONT

    missing = []
    for name in (DEFAULT_FONT, DEFAULT_BOLD_FONT, DEFAULT_HEAVY_FONT, DEFAULT_EMOJI_FONT):
        if not isinstance(get_font(name, 20), ImageFont.FreeTypeFont):
            missing.append(name)
    return missing


def _check_native_fonts() -> list[str]:
    """Names of configured fonts the NATIVE renderer cannot resolve.

    Pillow and Rust search for fonts independently, so Rust can miss a face Pillow finds (a
    different font dir inside the image, a wheel built against another layout). Rust does not fail
    on a miss — it renders sans-serif and counts the fallback — so probe it: render a tiny scene
    per font and read ``native_metrics["font_fallbacks"]``.
    """
    import json

    from src.sekai.skia_renderer.canvas import load_native_renderer
    from src.sekai.skia_renderer.ir_builder import IRBuilder
    from src.settings import (
        ASSETS_BASE_DIR,
        DEFAULT_BOLD_FONT,
        DEFAULT_EMOJI_FONT,
        DEFAULT_FONT,
        DEFAULT_HEAVY_FONT,
        FONT_DIR,
    )

    native = load_native_renderer()
    missing = []
    for name in (DEFAULT_FONT, DEFAULT_BOLD_FONT, DEFAULT_HEAVY_FONT, DEFAULT_EMOJI_FONT):
        builder = IRBuilder(
            8, 8, assets_base_dir=str(ASSETS_BASE_DIR), font_dir=str(FONT_DIR), default_font=name, bold_font=name
        )
        builder.text("A", (0, 0), size=8, role="default")
        result = native.render_scene(json.dumps(builder.build()).encode(), {})
        if (result.get("native_metrics") or {}).get("font_fallbacks"):
            missing.append(name)
    return missing


def _self_check_fonts() -> None:
    """Fail loudly at startup when a configured font cannot be resolved.

    A missing font is not a slow render — it is a WRONG one: every string comes out in the wrong
    face, on every image, silently, until someone notices by eye. The two layers need different
    answers:

    - **Pillow cannot resolve it** → BOTH backends are broken (Pillow degrades to a 10px bitmap
      face; Rust to sans-serif), so turning Skia off would fix nothing. Refuse to start. A deploy
      that fails fast beats one that serves thousands of wrong images.
    - **Only the native renderer cannot resolve it** → Pillow still renders correctly, so disable
      Skia and keep serving. This is the case the "refuse to enable Skia" rule is actually for.
    """
    missing_pillow = _check_pillow_fonts()
    if missing_pillow:
        raise RuntimeError(
            f"configured fonts cannot be resolved by Pillow: {missing_pillow} (font dir: {settings.font.dir}). "
            "Every rendered image would use the wrong face; refusing to start. Check the asset volume mount."
        )

    if not settings.drawing.use_skia_plot:
        return
    try:
        missing_native = _check_native_fonts()
    except ImportError:
        return  # no extension: already reported below, and Pillow renders everything
    except Exception:
        logger.exception("native font self-check failed to run; leaving Skia enabled")
        return

    if missing_native:
        settings.drawing.use_skia_plot = False
        logger.error(
            "DISABLING Skia: the native renderer cannot resolve %s (font dir: %s) and would render them in "
            "sans-serif, while Pillow resolves them correctly. Serving with Pillow.",
            missing_native,
            settings.font.dir,
        )
    else:
        logger.info("font self-check passed (Pillow and native renderer both resolve every configured font)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown events."""
    from src.core.heavy_render_pool import shutdown_heavy_render_worker_pool, startup_heavy_render_worker_pool
    from src.sekai.base.painter import Painter, shutdown_painter
    from src.sekai.base.utils import (
        cleanup_expired_composed_image_disk_cache,
        cleanup_expired_tmp_files,
        shutdown_utils,
    )
    from src.sekai.sk.drawer import shutdown_sk_drawer

    _ensure_nogil_runtime()
    # Configure coloredlogs
    coloredlogs.install(level="INFO", fmt=LOG_FORMAT, field_styles=FIELD_STYLE)
    configure_runtime_diagnostics()

    def _cleanup_disk_caches() -> None:
        composed_removed = cleanup_expired_composed_image_disk_cache()
        painter_removed = Painter.cleanup_old_disk_cache()
        if composed_removed or painter_removed:
            logger.info(
                "Cleaned drawing disk caches: composed=%d painter=%d",
                composed_removed,
                painter_removed,
            )

    # 后台定期清理临时文件
    async def _periodic_tmp_cleanup():
        while True:
            await asyncio.sleep(TMP_CLEANUP_INTERVAL)
            try:
                cleanup_expired_tmp_files()
            except Exception:
                logger.warning("Failed to cleanup tmp files", exc_info=True)

    # 后台定期清理磁盘缓存。内存缓存本身有 LRU/TTL，这里只处理长期运行服务中的落盘缓存。
    async def _periodic_disk_cache_cleanup():
        while True:
            await asyncio.sleep(DISK_CACHE_CLEANUP_INTERVAL)
            try:
                _cleanup_disk_caches()
            except Exception:
                logger.warning("Failed to cleanup drawing disk caches", exc_info=True)

    cleanup_tasks = [
        asyncio.create_task(_periodic_tmp_cleanup()),
        asyncio.create_task(_periodic_disk_cache_cleanup()),
    ]

    # Startup
    try:
        _cleanup_disk_caches()
    except Exception:
        logger.warning("Failed to cleanup drawing disk caches", exc_info=True)
    if settings.drawing.use_skia_plot:
        try:
            import haruki_skia_renderer  # noqa: F401
        except ImportError:
            logger.error(
                "Skia gates are enabled but haruki_skia_renderer is not importable; "
                "every Skia path will fall back to Pillow (fail-open)",
                exc_info=True,
            )
    _self_check_fonts()
    await startup_heavy_render_worker_pool()
    logger.info("Haruki Drawing API is starting...")
    yield
    # Shutdown
    logger.info("Haruki Drawing API is shutting down...")
    dump_runtime_diagnostics("lifespan_shutdown")
    for cleanup_task in cleanup_tasks:
        cleanup_task.cancel()
    await asyncio.gather(*cleanup_tasks, return_exceptions=True)
    await shutdown_heavy_render_worker_pool()
    shutdown_painter()
    shutdown_sk_drawer()
    shutdown_utils()
    logger.info("Resources cleaned up.")


app = FastAPI(
    title="Haruki Drawing API",
    description=_description,
    version="2.4.8",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

install_debug_middleware(app)


# ======================= Include Routers =======================

app.include_router(health.router)
app.include_router(pjsk_router)


if __name__ == "__main__":
    Granian("src.core.main:app", interface="asgi", address=SERVER_HOST, port=SERVER_PORT).serve()
