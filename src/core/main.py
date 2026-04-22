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
from src.core.pjsk import router as pjsk_router
from src.settings import (
    FIELD_STYLE,
    LOG_FORMAT,
    SERVER_HOST,
    SERVER_PORT,
)

logger = logging.getLogger(__name__)
_description = """
## 🎨 Haruki Drawing API

This API provides endpoints for generating various Project Sekai images.

### Available Modules:
- **Card**: Generate card detail, list, and box images
- **Music**: Generate music detail, list, progress, and rewards images
- **Profile**: Generate player profile images
- **Event**: Generate event detail, record, and list images
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown events."""
    from src.sekai.base.painter import shutdown_painter
    from src.sekai.base.utils import cleanup_expired_composed_image_disk_cache, cleanup_expired_tmp_files, shutdown_utils
    from src.sekai.sk.drawer import shutdown_sk_drawer

    _ensure_nogil_runtime()
    # Configure coloredlogs
    coloredlogs.install(level="INFO", fmt=LOG_FORMAT, field_styles=FIELD_STYLE)

    # 后台定期清理临时文件
    async def _periodic_tmp_cleanup():
        while True:
            await asyncio.sleep(TMP_CLEANUP_INTERVAL)
            try:
                cleanup_expired_tmp_files()
            except Exception:
                logger.warning("Failed to cleanup tmp files", exc_info=True)

    cleanup_task = asyncio.create_task(_periodic_tmp_cleanup())

    # Startup
    try:
        cleanup_expired_composed_image_disk_cache()
    except Exception:
        logger.warning("Failed to cleanup composed image disk cache", exc_info=True)
    logger.info("Haruki Drawing API is starting...")
    yield
    # Shutdown
    logger.info("Haruki Drawing API is shutting down...")
    cleanup_task.cancel()
    shutdown_painter()
    shutdown_sk_drawer()
    shutdown_utils()
    logger.info("Resources cleaned up.")


app = FastAPI(
    title="Haruki Drawing API",
    description=_description,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


# ======================= Include Routers =======================

app.include_router(health.router)
app.include_router(pjsk_router)


if __name__ == "__main__":
    Granian("src.core.main:app", interface="asgi", address=SERVER_HOST, port=SERVER_PORT).serve()
