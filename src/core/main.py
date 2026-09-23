"""
Haruki Drawing API - FastAPI Core Application

This module provides RESTful API endpoints for generating various Sekai images.
All endpoints accept JSON request bodies and return PNG images.

Run with: granian --interface asgi src.core.main:app
Swagger UI: http://localhost:8000/docs
ReDoc: http://localhost:8000/redoc
"""

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
import logging
from pathlib import Path
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
Every drawing endpoint returns the rendered image as ONE response body (`image/png` or `image/jpeg`).
When the request carries `X-Haruki-Artifact: 1` plus a valid render cache directive, the image is
uploaded to object storage and an `artifact_ref` JSON document is returned instead; any storage
failure falls back to image bytes with `X-Haruki-Artifact-Degraded: 1`. Every response carries
`X-Haruki-Node`.
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


def _cleanup_disk_caches() -> None:
    """Remove expired persistent cache entries and report a non-empty sweep."""

    from src.sekai.base.painter_cache import cleanup_painter_disk_cache
    from src.sekai.base.utils import cleanup_expired_composed_image_disk_cache
    from src.sekai.profile.custom_profile.diagnostics import cleanup_custom_profile_diagnostics

    composed_removed = cleanup_expired_composed_image_disk_cache()
    painter_removed = cleanup_painter_disk_cache()
    diagnostic_removed = cleanup_custom_profile_diagnostics()
    if composed_removed or painter_removed or diagnostic_removed:
        logger.info(
            "Cleaned drawing disk caches: composed=%d painter=%d custom_profile_diagnostics=%d",
            composed_removed,
            painter_removed,
            diagnostic_removed,
        )


async def _periodic_cleanup(interval_seconds: float, cleanup: Callable[[], object], warning: str) -> None:
    """Run one synchronous cleanup forever without letting a failed sweep kill the task."""

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            cleanup()
        except Exception:
            logger.warning(warning, exc_info=True)


async def _periodic_pool_task(interval_seconds: float, func: Callable[[], object], warning: str) -> None:
    """Like `_periodic_cleanup`, but runs the sync job on the default render pool (filesystem walks)."""
    from src.sekai.base.utils import run_in_pool

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await run_in_pool(func)
        except Exception:
            logger.warning(warning, exc_info=True)


def _sweep_asset_mirror() -> None:
    """One mirror sweep (version dirs, stale `.tmp`, byte/entry caps); a disabled mirror is skipped."""
    from src.assets.mirror import AssetMirror, get_asset_mirror
    from src.assets.sweeper import MirrorSweeper

    mirror = get_asset_mirror()
    if not isinstance(mirror, AssetMirror):
        return
    config = settings.assets.mirror
    result = MirrorSweeper(
        root=mirror.mirror_root,
        current_version=lambda: mirror.version,
        max_bytes=config.max_bytes,
        max_entries=config.max_entries,
        versions_keep=config.versions_keep,
        tmp_max_age_seconds=config.tmp_max_age_seconds,
        stats=mirror.stats,
        mirror_dir=mirror.mirror_dir,
    ).sweep_once()
    if result.versions_removed or result.evicted_entries:
        logger.info(
            "mirror.sweep versions_removed=%d evicted_entries=%d evicted_bytes=%d entries=%d bytes=%d",
            result.versions_removed,
            result.evicted_entries,
            result.evicted_bytes,
            result.entries,
            result.bytes,
        )


def _poll_asset_mirror_version() -> None:
    """Re-read the manifest version source and swap the mirror layout when it changed."""
    from src.assets.mirror import get_asset_mirror

    get_asset_mirror().refresh_version()


def _create_cleanup_tasks() -> list[asyncio.Task[None]]:
    from src.sekai.base.utils import cleanup_expired_tmp_files

    tasks = [
        asyncio.create_task(
            _periodic_cleanup(TMP_CLEANUP_INTERVAL, cleanup_expired_tmp_files, "Failed to cleanup tmp files")
        ),
        asyncio.create_task(
            _periodic_cleanup(
                DISK_CACHE_CLEANUP_INTERVAL,
                _cleanup_disk_caches,
                "Failed to cleanup drawing disk caches",
            )
        ),
    ]
    if settings.assets.source == "mirror":
        mirror_config = settings.assets.mirror
        tasks.append(
            asyncio.create_task(
                _periodic_pool_task(
                    max(1.0, float(mirror_config.sweep_interval_seconds)),
                    _sweep_asset_mirror,
                    "Failed to sweep the asset mirror",
                )
            )
        )
        tasks.append(
            asyncio.create_task(
                _periodic_pool_task(
                    max(1.0, float(mirror_config.manifest_version_poll_seconds)),
                    _poll_asset_mirror_version,
                    "Failed to poll the asset mirror manifest version",
                )
            )
        )
    return tasks


def _run_initial_disk_cleanup() -> None:
    try:
        _cleanup_disk_caches()
    except Exception:
        logger.warning("Failed to cleanup drawing disk caches", exc_info=True)


_CUSTOM_PROFILE_DIR_FIELDS = (
    "custom_profile_assets_dir",
    "custom_profile_fonts_dir",
    "custom_profile_shape_sprite_dir",
    "custom_profile_unity_ui_sprite_dir",
)


def _missing_custom_profile_dirs() -> list[str]:
    """Custom-profile directories (expanded per region) that do not exist; they are local-only by contract."""
    from src.sekai.profile.custom_profile.resource_paths import REGION_CODES

    missing: list[str] = []
    for name in _CUSTOM_PROFILE_DIR_FIELDS:
        configured = getattr(settings.drawing, name, None)
        if configured is None:
            continue
        raw = str(configured)
        paths = [raw.replace("{region}", region) for region in sorted(REGION_CODES)] if "{region}" in raw else [raw]
        missing.extend(f"{name}={path}" for path in paths if not Path(path).is_dir())
    return missing


def _start_asset_mirror() -> None:
    """Start the asset mirror (never fatal) and warn when mirror mode runs without the local-only dirs."""
    from src.assets.mirror import start_asset_mirror

    start_asset_mirror()
    if settings.assets.source != "mirror":
        return
    try:
        missing = _missing_custom_profile_dirs()
    except Exception:
        logger.warning("custom-profile directory check failed", exc_info=True)
        return
    if missing:
        logger.warning(
            "assets.source=mirror but custom-profile directories are missing (they are never mirrored; "
            "keep rsyncing them): %s",
            ", ".join(missing),
        )


async def _start_artifact_runtime() -> None:
    """Build the artifact runtime and warm its index; a failure never fails boot (bytes mode keeps serving)."""
    from src.artifact.runtime import startup_artifact_runtime

    try:
        await startup_artifact_runtime()
    except Exception:
        logger.error("artifact runtime startup failed; serving image bytes only", exc_info=True)


async def _stop_artifact_runtime() -> None:
    from src.artifact.runtime import shutdown_artifact_runtime

    try:
        await shutdown_artifact_runtime()
    except Exception:
        logger.warning("artifact runtime shutdown failed", exc_info=True)


def _start_user_upload_store() -> None:
    """Build the user-upload store (profile backgrounds); a failure never fails boot (local disk keeps serving)."""
    from src.assets.user_upload import start_user_upload_store

    start_user_upload_store()


async def _stop_user_upload_store() -> None:
    from src.assets.user_upload import shutdown_user_upload_store

    try:
        await shutdown_user_upload_store()
    except Exception:
        logger.warning("user-upload store shutdown failed", exc_info=True)


async def _startup_runtime() -> list[asyncio.Task[None]]:
    from src.core.heavy_render_pool import startup_heavy_render_worker_pool

    _ensure_nogil_runtime()
    coloredlogs.install(level="INFO", fmt=LOG_FORMAT, field_styles=FIELD_STYLE)
    configure_runtime_diagnostics()
    _self_check_fonts()
    _start_asset_mirror()
    _start_user_upload_store()
    cleanup_tasks = _create_cleanup_tasks()
    _run_initial_disk_cleanup()
    await startup_heavy_render_worker_pool()
    await _start_artifact_runtime()
    logger.info("Haruki Drawing API is starting...")
    return cleanup_tasks


async def _shutdown_runtime(cleanup_tasks: list[asyncio.Task[None]]) -> None:
    from src.assets.mirror import shutdown_asset_mirror
    from src.core.heavy_render_pool import shutdown_heavy_render_worker_pool
    from src.sekai.base.painter_cache import cleanup_painter_disk_cache
    from src.sekai.base.utils import shutdown_utils

    logger.info("Haruki Drawing API is shutting down...")
    dump_runtime_diagnostics("lifespan_shutdown")
    for cleanup_task in cleanup_tasks:
        cleanup_task.cancel()
    await asyncio.gather(*cleanup_tasks, return_exceptions=True)
    await shutdown_heavy_render_worker_pool()
    await _stop_artifact_runtime()
    await _stop_user_upload_store()
    shutdown_asset_mirror()
    cleanup_painter_disk_cache()
    shutdown_utils()
    logger.info("Resources cleaned up.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown events."""

    cleanup_tasks = await _startup_runtime()
    yield
    await _shutdown_runtime(cleanup_tasks)


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
