"""`ArtifactService`: hash -> upload -> index write -> `ArtifactOutcome` (plan §8.4).

Fail open (invariant I1): every failure that is not "the caller asked and storage succeeded" becomes a
degraded outcome, and the exit returns image bytes. `index_written=False` is never an error: the object is
durable in the bucket and the ref is valid; only the request-key mapping is missing.

There is no content-hash lookup before the upload. Object keys are content-addressed
(`pjsk/<api_path>/<sha256>.<ext>`), so re-putting bytes that are already stored rewrites the same object, and
the lookup never hit in production (pages embed the render time) while it cost two round trips to PostgreSQL
on every request. The dedup rule of addendum A2 now runs inside the one-statement index write: when the hash
already had a `garage` row, that row keeps its `cdn_path`, the write returns it, and the ref carries it
(`reused=true`). If that stored path names another endpoint's key (or a Cloud `pjsk/<sha256>.<ext>` row),
the object just uploaded under this request's key is not recorded in the index; it is counted as
`reused_unrecorded_objects` because Cloud's GC, which deletes only recorded paths, will not remove it.

Every sub-stage is timed into `ArtifactStats` (`/render-stats`) and into `ArtifactOutcome.stages` (the
`image.response` line): `hash`, `upload`, `index_write` (which contains `index_acquire`, and `index_connect`
on a loop's first write), and `total`.

Neither `opendal` nor `asyncpg` is imported here; the store and index arrive already built.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import logging
import time
from typing import TYPE_CHECKING, Any

from src.artifact.ref import (
    ARTIFACT_KIND,
    STORAGE_BACKEND_GARAGE,
    ArtifactRef,
    build_object_key,
    expires_at_for,
    extension_for_media_type,
    format_rfc3339,
    is_foreign_cdn_path,
)
from src.index.protocols import ContentRow, IndexSchemaError, RequestRow
from src.storage.protocols import StorageError

if TYPE_CHECKING:  # pragma: no cover
    from src.artifact.directive import RenderCacheDirective
    from src.artifact.stats import ArtifactStats
    from src.core.image_payload import EncodedImagePayload
    from src.index.protocols import RecordResult, RenderIndex
    from src.settings import StorageSettings
    from src.storage.protocols import ObjectStore

logger = logging.getLogger("src.artifact.service")

INDEX_WRITE_LOG_INTERVAL_SECONDS = 60.0

Offload = Callable[..., Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ArtifactOutcome:
    ref: ArtifactRef | None
    degraded: bool
    reason: str  # "ok" | "reused" | "disabled" | "runtime_unavailable" | "upload_failed"
    # | "upload_timeout" | "unsupported_media" | "internal"
    # Seconds per artifact sub-stage of this request, for the `image.response` line; not part of equality.
    stages: dict[str, float] = field(default_factory=dict, compare=False)


def _set_stage(stage: str) -> None:
    from src.core.debug import set_request_stage  # lazy: src.core depends on src.artifact, not the reverse

    set_request_stage(stage)


async def _run_in_render_pool(func: Callable[..., Any], *args: Any) -> Any:
    from src.sekai.base.utils import run_in_pool  # lazy: keeps src.artifact free of the renderer import tree

    return await run_in_pool(func, *args)


def _sha256_hex(hasher: Callable[..., Any], data: memoryview) -> str:
    return hasher(data).hexdigest()


class ArtifactService:
    """One per process. Coroutines only; every I/O step runs on the event loop with a timeout."""

    def __init__(
        self,
        *,
        store: ObjectStore | None,
        index: RenderIndex | None,
        settings: StorageSettings,
        node_name: str,
        stats: ArtifactStats,
        hasher: Callable[..., Any] = hashlib.sha256,
        clock: Callable[[], float] = time.perf_counter,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        offload: Offload | None = None,
    ) -> None:
        self._store = store
        self._index = index if settings.index.enabled else None
        self._settings = settings
        self._node_name = node_name
        self._stats = stats
        self._hasher = hasher
        self._clock = clock
        self._now = now
        self._offload: Offload = offload or _run_in_render_pool
        self._upload_slots: asyncio.Semaphore | None = None
        self._upload_slots_loop: asyncio.AbstractEventLoop | None = None
        self._index_retry_at = 0.0
        self._index_unusable_reason = "unavailable"
        self._last_write_log: dict[str, float] = {}
        self._stats.set_index_state(configured=self._index is not None, usable=self._index is not None)

    # ------------------------------------------------------------------ helpers

    @property
    def store(self) -> ObjectStore | None:
        return self._store

    @property
    def index(self) -> RenderIndex | None:
        return self._index

    def _slots(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._upload_slots is None or self._upload_slots_loop is not loop:
            self._upload_slots = asyncio.Semaphore(max(1, int(self._settings.upload_concurrency)))
            self._upload_slots_loop = loop
        return self._upload_slots

    def _command_timeout(self) -> float:
        return max(0.001, float(self._settings.index.command_timeout_seconds))

    def _index_state(self) -> str:
        """`usable`, or the `index_skipped` reason this request would count."""
        if self._index is None:
            return "disabled"
        if self._clock() < self._index_retry_at:
            return self._index_unusable_reason
        return "usable"

    def _mark_index_unusable(self, stage: str, exc: BaseException) -> None:
        reason = "schema_missing" if isinstance(exc, IndexSchemaError) else "unavailable"
        self._index_unusable_reason = reason
        self._index_retry_at = self._clock() + max(0.0, float(self._settings.index.connect_retry_seconds))
        self._stats.set_index_state(configured=True, usable=False, error=(stage, exc))

    def _mark_index_usable(self) -> None:
        self._stats.set_index_state(configured=True, usable=True)

    def _degraded(self, reason: str, stages: dict[str, float]) -> ArtifactOutcome:
        self._stats.degraded(reason)
        return ArtifactOutcome(ref=None, degraded=True, reason=reason, stages=stages)

    def _stage_done(self, stages: dict[str, float], name: str, started: float) -> float:
        elapsed = max(0.0, self._clock() - started)
        stages[name] = elapsed
        self._stats.record_stage(name, elapsed)
        return elapsed

    def _stage_value(self, stages: dict[str, float], name: str, elapsed: float) -> None:
        elapsed = max(0.0, float(elapsed))
        stages[name] = elapsed
        self._stats.record_stage(name, elapsed)

    def _log_write_failure(self, content_hash: str, cache_key: str, exc: BaseException) -> None:
        name = type(exc).__name__
        now = self._clock()
        last = self._last_write_log.get(name)
        if last is not None and now - last < INDEX_WRITE_LOG_INTERVAL_SECONDS:
            return
        self._last_write_log[name] = now
        logger.error("artifact.index_write_failed hash=%s key=%s exc=%s: %s", content_hash, cache_key, name, exc)

    # ------------------------------------------------------------------ steps

    async def _hash(self, data: bytes, stages: dict[str, float]) -> str:
        _set_stage("artifact:hash")
        started = self._clock()
        view = memoryview(data)
        if len(data) >= int(self._settings.hash_in_pool_min_bytes):
            digest = await self._offload(_sha256_hex, self._hasher, view)
        else:
            digest = _sha256_hex(self._hasher, view)
        self._stage_done(stages, "hash", started)
        return digest

    async def _upload(
        self, store: ObjectStore, object_key: str, payload: EncodedImagePayload, stages: dict[str, float]
    ) -> float | str:
        """Elapsed seconds on success, else the degraded reason."""
        _set_stage("artifact:upload")
        started = self._clock()
        try:
            async with self._slots():
                await asyncio.wait_for(
                    store.write(object_key, memoryview(payload.image_bytes), content_type=payload.media_type),
                    float(self._settings.upload_timeout_seconds),
                )
        except TimeoutError as exc:
            self._stats.incr("upload_timeouts")
            self._stats.record_error("upload", exc if str(exc) else "upload timed out")
            logger.warning("artifact.upload_timeout key=%s bucket=%s", object_key, store.bucket)
            return "upload_timeout"
        except StorageError as exc:
            self._stats.incr("upload_failures")
            self._stats.record_error("upload", exc)
            logger.warning(
                "artifact.upload_failed key=%s bucket=%s exc=%s: %s", object_key, store.bucket, type(exc).__name__, exc
            )
            return "upload_failed"
        finally:
            elapsed = self._stage_done(stages, "upload", started)
        self._stats.incr("uploads")
        self._stats.incr("upload_bytes", len(payload.image_bytes))
        self._stats.add_upload_elapsed(elapsed)
        return elapsed

    async def _record(self, content: ContentRow, request: RequestRow, stages: dict[str, float]) -> RecordResult | None:
        assert self._index is not None
        _set_stage("artifact:index_write")
        started = self._clock()
        try:
            result = await asyncio.wait_for(self._index.record(content, request), self._command_timeout())
        except Exception as exc:
            self._stats.incr("index_write_failures")
            self._stats.record_error("index_write", exc)
            self._mark_index_unusable("index_write", exc)
            self._log_write_failure(content.hash, request.request_key, exc)
            return None
        finally:
            self._stage_done(stages, "index_write", started)
        self._stage_value(stages, "index_acquire", result.acquire_seconds)
        if result.connect_seconds > 0.0:
            self._stage_value(stages, "index_connect", result.connect_seconds)
        self._stats.incr("index_writes")
        self._mark_index_usable()
        return result

    # ------------------------------------------------------------------ entry point

    async def process(self, payload: EncodedImagePayload, directive: RenderCacheDirective) -> ArtifactOutcome:
        stages: dict[str, float] = {}
        started = self._clock()
        try:
            outcome = await self._process(payload, directive, stages)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "artifact.internal_error api_path=%s exc=%s", directive.api_path, type(exc).__name__, exc_info=True
            )
            self._stats.record_error("internal", exc)
            outcome = self._degraded("internal", stages)
        self._stage_done(stages, "total", started)
        return outcome

    async def _process(
        self, payload: EncodedImagePayload, directive: RenderCacheDirective, stages: dict[str, float]
    ) -> ArtifactOutcome:
        store = self._store
        if store is None:
            return self._degraded("runtime_unavailable", stages)
        if extension_for_media_type(payload.media_type) is None:
            logger.warning(
                "artifact.unsupported_media media_type=%s api_path=%s", payload.media_type, directive.api_path
            )
            return self._degraded("unsupported_media", stages)

        content_hash = await self._hash(payload.image_bytes, stages)
        object_key = build_object_key(directive.api_path, content_hash, payload.media_type)
        uploaded = await self._upload(store, object_key, payload, stages)
        if isinstance(uploaded, str):
            return self._degraded(uploaded, stages)  # A3: no index write after a failed upload

        cdn_path = object_key
        media_type = payload.media_type
        size_bytes = len(payload.image_bytes)
        reused = False
        index_written = False
        expires = expires_at_for(self._now(), directive.ttl_seconds)
        if directive.store:
            state = self._index_state()
            if state == "usable":
                content = ContentRow(
                    hash=content_hash,
                    group_name=directive.group,
                    cdn_path=object_key,
                    storage_backend=STORAGE_BACKEND_GARAGE,
                    media_type=media_type,
                    size_bytes=size_bytes,
                    expires_at=expires,
                )
                request = RequestRow(
                    request_key=directive.cache_key,
                    content_hash=content_hash,
                    api_path=directive.api_path,
                    user_id=directive.user_id,
                    group_name=directive.group,
                    key_version=directive.key_version,
                    ttl_seconds=directive.ttl_seconds,
                    expires_at=expires,
                )
                result = await self._record(content, request, stages)
                index_written = result is not None
                if result is not None and result.prior_backend == STORAGE_BACKEND_GARAGE:
                    # A2: an existing garage row keeps its path; the ref names what the index names.
                    reused = True
                    cdn_path = result.cdn_path
                    media_type = result.media_type or media_type
                    size_bytes = result.size_bytes if result.size_bytes is not None else size_bytes
                    if cdn_path != object_key:
                        self._stats.incr("reused_unrecorded_objects")
                    if is_foreign_cdn_path(cdn_path):
                        self._stats.incr("reused_foreign")
            else:
                self._stats.index_skipped(state)

        self._stats.incr("reused" if reused else "published")
        ref = ArtifactRef(
            kind=ARTIFACT_KIND,
            hash=content_hash,
            cdn_path=cdn_path,
            storage_backend=STORAGE_BACKEND_GARAGE,
            bucket=store.bucket,
            object_key=cdn_path,
            size_bytes=size_bytes,
            media_type=media_type,
            width=payload.image_width,
            height=payload.image_height,
            cache_key=directive.cache_key,
            ttl_seconds=directive.ttl_seconds,
            expires_at=format_rfc3339(expires),
            reused=reused,
            index_written=index_written,
            upload_elapsed=uploaded,
            node_name=self._node_name,
        )
        return ArtifactOutcome(ref=ref, degraded=False, reason="reused" if reused else "ok", stages=stages)
