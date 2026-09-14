"""`ArtifactRuntime`: the fail-open composition root of the artifact output (plan §8.5).

`get_artifact_runtime()` is lazy and lock-guarded, so the three harnesses that never run lifespan
(`httpx.ASGITransport`, a subprocess `TestClient`, spawned heavy workers) still get a working — usually
disabled — runtime. Building never raises for a bad remote: storage disabled yields a disabled runtime that
never imports `opendal`; a provider validation or `opendal` import failure yields an unavailable runtime that
is rebuilt after `index.connect_retry_seconds`; a missing DSN keeps uploads working with `index_written=false`.
`/ready` is never influenced by storage or index health.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from src.artifact.service import ArtifactOutcome, ArtifactService
from src.artifact.stats import ArtifactStats, artifact_node_name, artifact_stats

if TYPE_CHECKING:  # pragma: no cover
    from src.artifact.directive import RenderCacheDirective
    from src.core.image_payload import EncodedImagePayload
    from src.index.protocols import RenderIndex
    from src.settings import Settings, StorageSettings
    from src.storage.protocols import ObjectStore

logger = logging.getLogger("src.artifact.runtime")

STARTUP_PREFLIGHT_TIMEOUT_SECONDS = 3.0

StoreFactory = Callable[["StorageSettings"], "ObjectStore"]
IndexFactory = Callable[["StorageSettings"], "RenderIndex | None"]


class ArtifactRuntime:
    """Holds the service, store and index. `service is None` means disabled or unavailable (`reason`)."""

    def __init__(
        self,
        *,
        service: ArtifactService | None = None,
        store: ObjectStore | None = None,
        index: RenderIndex | None = None,
        reason: str = "ok",
        retry_at: float | None = None,
        stats: ArtifactStats | None = None,
    ) -> None:
        self.service = service
        self.store = store
        self.index = index
        self.reason = reason if service is None else "ok"
        self.retry_at = retry_at
        self.stats = stats or artifact_stats
        self.closed = False

    @property
    def enabled(self) -> bool:
        return self.service is not None

    def should_rebuild(self, now: float) -> bool:
        return self.service is None and self.retry_at is not None and now >= self.retry_at

    async def process(self, payload: EncodedImagePayload, directive: RenderCacheDirective) -> ArtifactOutcome:
        if self.service is None:
            self.stats.degraded(self.reason)
            return ArtifactOutcome(ref=None, degraded=True, reason=self.reason)
        return await self.service.process(payload, directive)

    async def close(self) -> None:
        """Close the index pool and the store. Idempotent, never raises."""
        if self.closed:
            return
        self.closed = True
        for name, resource in (("index", self.index), ("store", self.store)):
            if resource is None:
                continue
            try:
                await resource.close()
            except Exception as exc:
                logger.warning("artifact.%s_close_failed exc=%s: %s", name, type(exc).__name__, exc)


def _default_store_factory(storage: StorageSettings) -> ObjectStore:
    from src.storage.opendal_store import OpendalObjectStore
    from src.storage.provider import opendal_kwargs

    opendal_kwargs(storage.provider, None)  # validate the provider block (e.g. missing bucket) without I/O
    return OpendalObjectStore.from_provider(
        storage.provider,
        region=None,
        timeout=storage.upload_timeout_seconds,
        io_timeout=storage.upload_io_timeout_seconds,
        retries=storage.upload_retries,
        concurrency=storage.upload_concurrency,
        name="artifact-store",
    )


def _default_index_factory(storage: StorageSettings) -> RenderIndex | None:
    index_settings = storage.index
    if not index_settings.enabled:
        logger.info("artifact index disabled by settings (storage.index.enabled=false); index_written=false")
        return None
    dsn = index_settings.dsn.get_secret_value().strip() if index_settings.dsn is not None else ""
    if not dsn:
        logger.warning("artifact index disabled: HARUKI_STORAGE__INDEX__DSN is not set; uploads only")
        return None
    from src.index.asyncpg_index import AsyncpgRenderIndex  # does not import asyncpg

    index = AsyncpgRenderIndex(dsn, index_settings)
    logger.info("artifact index configured dsn=%s", index.dsn_redacted)
    return index


def build_artifact_runtime(
    settings: Settings,
    *,
    store_factory: StoreFactory | None = None,
    index_factory: IndexFactory | None = None,
    node_name: str | None = None,
    stats: ArtifactStats | None = None,
    clock: Callable[[], float] = time.monotonic,
    service_kwargs: dict[str, Any] | None = None,
) -> ArtifactRuntime:
    """Pure factory; never raises for a bad remote or a bad provider block."""
    storage = settings.storage
    stats = stats or artifact_stats
    if not storage.enabled:
        stats.set_runtime_state(enabled=False)
        stats.set_index_state(configured=False, usable=False)
        return ArtifactRuntime(reason="disabled", stats=stats)

    provider = storage.provider
    try:
        store = (store_factory or _default_store_factory)(storage)
    except Exception as exc:
        retry_seconds = max(0.0, float(storage.index.connect_retry_seconds))
        logger.error(
            "artifact.runtime_unavailable exc=%s: %s provider=%s (returning image bytes; retry in %.0fs)",
            type(exc).__name__,
            exc,
            provider.describe(),
            retry_seconds,
        )
        stats.set_runtime_state(enabled=False, bucket=provider.bucket)
        stats.record_error("runtime", exc)
        return ArtifactRuntime(reason="runtime_unavailable", retry_at=clock() + retry_seconds, stats=stats)

    if provider.root:
        logger.warning(
            "artifact provider root=%r is ignored: artifact keys are pjsk/<api_path>/<sha256>.<ext> at the bucket "
            "root and Cloud reads cdn_path verbatim (keep storage.provider.root empty)",
            provider.root,
        )

    try:
        index = (index_factory or _default_index_factory)(storage)
    except Exception as exc:
        logger.error("artifact index construction failed exc=%s: %s; uploads only", type(exc).__name__, exc)
        index = None

    service = ArtifactService(
        store=store,
        index=index,
        settings=storage,
        node_name=node_name if node_name is not None else artifact_node_name(),
        stats=stats,
        **(service_kwargs or {}),
    )
    stats.set_runtime_state(enabled=True, bucket=store.bucket)
    logger.info("artifact runtime enabled provider=%s index=%s", provider.describe(), index is not None)
    return ArtifactRuntime(service=service, store=store, index=service.index, stats=stats)


# ---------------------------------------------------------------------- process-wide accessor
_artifact_runtime: ArtifactRuntime | None = None
_artifact_runtime_lock = threading.Lock()
_clock: Callable[[], float] = time.monotonic


def get_artifact_runtime() -> ArtifactRuntime:
    """Lazy process-wide runtime; an unavailable runtime is rebuilt once its retry window has passed."""
    global _artifact_runtime
    runtime = _artifact_runtime
    if runtime is not None and not runtime.should_rebuild(_clock()):
        return runtime
    with _artifact_runtime_lock:
        runtime = _artifact_runtime
        if runtime is None or runtime.should_rebuild(_clock()):
            from src.settings import settings

            _artifact_runtime = build_artifact_runtime(settings, clock=_clock)
        return _artifact_runtime


def set_artifact_runtime(runtime: ArtifactRuntime | None) -> None:
    """Install `runtime` (test seam); `None` resets to lazy construction. Does not close the old one."""
    global _artifact_runtime
    with _artifact_runtime_lock:
        _artifact_runtime = runtime


async def startup_artifact_runtime() -> None:
    """Lifespan startup: build the runtime and warm the index (preflight, capped). Never raises."""
    try:
        runtime = get_artifact_runtime()
    except Exception as exc:  # build is already fail-open; this guards a bug in the accessor itself
        logger.error("artifact.startup_failed exc=%s: %s", type(exc).__name__, exc)
        return
    if not runtime.enabled:
        logger.info("artifact runtime not active reason=%s (image bytes only)", runtime.reason)
        return
    index = runtime.index
    if index is None:
        return
    try:
        await asyncio.wait_for(index.preflight(), STARTUP_PREFLIGHT_TIMEOUT_SECONDS)
    except Exception as exc:
        runtime.stats.set_index_state(configured=True, usable=False, error=("preflight", exc))
        logger.warning(
            "artifact index preflight failed at startup exc=%s (index writes back off; uploads continue)",
            type(exc).__name__,
        )
        return
    runtime.stats.set_index_state(configured=True, usable=True)
    logger.info("artifact index preflight ok")


async def shutdown_artifact_runtime() -> None:
    """Lifespan shutdown: close the installed runtime. Never builds one, never raises, idempotent."""
    with _artifact_runtime_lock:
        runtime = _artifact_runtime
    if runtime is None:
        return
    try:
        await runtime.close()
    except Exception as exc:  # close() already swallows; defensive
        logger.warning("artifact.shutdown_failed exc=%s: %s", type(exc).__name__, exc)
