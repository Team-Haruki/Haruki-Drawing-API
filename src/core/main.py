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
    PROJECT_ROOT,
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


def _check_native_fonts(*, emoji: bool = False) -> list[str]:
    """Probe configured text faces (or emoji) through native raster font resolution.

    Rendering a tiny scene catches fallback to sans-serif even when a file exists
    but cannot be loaded. An explicit alphabetic baseline avoids consulting the
    Python layout engine and its Pillow recovery path during this probe.
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
    names = (DEFAULT_EMOJI_FONT,) if emoji else (DEFAULT_FONT, DEFAULT_BOLD_FONT, DEFAULT_HEAVY_FONT)
    for name in names:
        builder = IRBuilder(
            8, 8, assets_base_dir=str(ASSETS_BASE_DIR), font_dir=str(FONT_DIR), default_font=name, bold_font=name
        )
        # Probe raster font resolution without Python layout or its legacy fallback.
        builder.text("😀" if emoji else "A", (0, 0), size=8, role="default", baseline="alphabetic")
        result = native.render_scene(json.dumps(builder.build()).encode(), {})
        if (result.get("native_metrics") or {}).get("font_fallbacks"):
            missing.append(name)
    return missing


def _self_check_fonts() -> None:
    """Require the current native renderer and configured text faces before serving."""
    if not settings.drawing.use_skia_plot:
        raise RuntimeError("Native rendering is required; HARUKI_DRAWING__USE_SKIA_PLOT must be true")
    try:
        missing = _check_native_fonts()
    except Exception as exc:
        raise RuntimeError(f"Native renderer startup check failed: {exc}") from exc
    if missing:
        raise RuntimeError(
            f"configured text fonts cannot be resolved: {missing} (font dir: {settings.font.dir}). "
            "Refusing to start; check the asset volume mount."
        )
    try:
        missing_emoji = _check_native_fonts(emoji=True)
        if missing_emoji:
            logger.error("native emoji font cannot be resolved: %s; text rendering remains available", missing_emoji)
    except Exception:
        logger.exception("native emoji font probe failed; text rendering remains available")
    logger.info("font self-check passed (native renderer resolves every configured text font)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown events."""
    from src.core.heavy_render_pool import shutdown_heavy_render_worker_pool, startup_heavy_render_worker_pool
    from src.sekai.base.painter_cache import cleanup_painter_disk_cache
    from src.sekai.base.utils import (
        cleanup_expired_composed_image_disk_cache,
        cleanup_expired_tmp_files,
        shutdown_utils,
    )

    _ensure_nogil_runtime()
    # Configure coloredlogs
    coloredlogs.install(level="INFO", fmt=LOG_FORMAT, field_styles=FIELD_STYLE)
    configure_runtime_diagnostics()
    # Fail before allocating cleanup tasks or spawning workers.
    _self_check_fonts()

    def _cleanup_disk_caches() -> None:
        composed_removed = cleanup_expired_composed_image_disk_cache()
        painter_removed = cleanup_painter_disk_cache()
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
    cleanup_painter_disk_cache()
    shutdown_utils()
    logger.info("Resources cleaned up.")


def _app_version() -> str:
    """The version /docs and openapi.json advertise, read from the one place that defines it.

    It used to be hardcoded here AND in pyproject.toml, and both drifted: the app reported 2.4.8
    while the repo had shipped v2.5.0 through v2.5.3. Releases are cut from a git tag and nothing
    read either constant, so nobody noticed for five releases.
    """
    try:
        import tomllib

        with open(PROJECT_ROOT / "pyproject.toml", "rb") as fh:
            return str(tomllib.load(fh)["project"]["version"])
    except Exception:  # pyproject not shipped / unreadable — never fail a boot over a version string
        return "unknown"


app = FastAPI(
    title="Haruki Drawing API",
    description=_description,
    version=_app_version(),
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
