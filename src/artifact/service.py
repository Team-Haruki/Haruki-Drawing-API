"""`ArtifactService`: hash -> index lookup -> upload -> index write -> `ArtifactOutcome` (plan §8.4).

Fail open (invariant I1): every failure that is not "the caller asked and storage succeeded" becomes a
degraded outcome, and the exit returns image bytes. A ref is returned only after the shared content lock,
object write and both index rows have committed. A durable upload intent covers failures before commit.

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
from uuid import uuid4

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
from src.index.protocols import ContentRow, IndexSchemaError, IndexUnavailable, RequestRow
from src.storage.protocols import StorageError

if TYPE_CHECKING:  # pragma: no cover
    from src.artifact.directive import RenderCacheDirective
    from src.artifact.stats import ArtifactStats
    from src.core.image_payload import EncodedImagePayload
    from src.index.protocols import ContentWriter, RenderIndex
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
        self._stage_value(stages, name, elapsed)
        return elapsed

    def _stage_value(self, stages: dict[str, float], name: str, elapsed: float) -> None:
        elapsed = max(0.0, float(elapsed))
        stages[name] = stages.get(name, 0.0) + elapsed
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

    async def _lookup(self, writer: ContentWriter, content_hash: str, stages: dict[str, float]) -> ContentRow | None:
        assert self._index is not None
        _set_stage("artifact:index_lookup")
        started = self._clock()
        self._stats.incr("index_lookups")
        try:
            row = await asyncio.wait_for(writer.lookup_content(content_hash), self._command_timeout())
        except Exception as exc:
            self._stats.incr("index_lookup_errors")
            self._stats.record_error("index_lookup", exc)
            self._mark_index_unusable("index_lookup", exc)
            logger.warning("artifact.index_lookup_failed hash=%s exc=%s: %s", content_hash, type(exc).__name__, exc)
            if isinstance(exc, (IndexUnavailable, IndexSchemaError)):
                raise
            raise IndexUnavailable("content lookup failed") from exc
        else:
            self._mark_index_usable()
            if row is not None and row.storage_backend == STORAGE_BACKEND_GARAGE:
                self._stats.incr("index_lookup_hits")
        self._stage_done(stages, "index_lookup", started)
        return row

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

    async def _record(
        self, writer: ContentWriter, content: ContentRow, request: RequestRow, stages: dict[str, float]
    ) -> bool:
        assert self._index is not None
        _set_stage("artifact:index_write")
        started = self._clock()
        try:
            await asyncio.wait_for(writer.record(content, request), self._command_timeout())
        except Exception as exc:
            self._stats.incr("index_write_failures")
            self._stats.record_error("index_write", exc)
            self._mark_index_unusable("index_write", exc)
            self._log_write_failure(content.hash, request.request_key, exc)
            if isinstance(exc, (IndexUnavailable, IndexSchemaError)):
                raise
            raise IndexUnavailable("content write failed") from exc
        finally:
            self._stage_done(stages, "index_write", started)
        self._mark_index_usable()
        return True

    # ------------------------------------------------------------------ entry point

    async def process(self, payload: EncodedImagePayload, directive: RenderCacheDirective) -> ArtifactOutcome:
        stages: dict[str, float] = {}
        started = self._clock()
        try:
            return await self._process(payload, directive, stages)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "artifact.internal_error api_path=%s exc=%s", directive.api_path, type(exc).__name__, exc_info=True
            )
            self._stats.record_error("internal", exc)
            return self._degraded("internal", stages)
        finally:
            self._stage_done(stages, "total", started)

    async def _process(
        self, payload: EncodedImagePayload, directive: RenderCacheDirective, stages: dict[str, float]
    ) -> ArtifactOutcome:
        if not directive.store:
            return ArtifactOutcome(ref=None, degraded=False, reason="store_disabled", stages=stages)
        store = self._store
        if store is None:
            return self._degraded("runtime_unavailable", stages)
        if extension_for_media_type(payload.media_type) is None:
            logger.warning(
                "artifact.unsupported_media media_type=%s api_path=%s", payload.media_type, directive.api_path
            )
            return self._degraded("unsupported_media", stages)

        content_hash = await self._hash(payload.image_bytes, stages)

        state = self._index_state()
        if state != "usable":
            self._stats.index_skipped(state)
            return self._degraded("index_unavailable", stages)
        assert self._index is not None
        candidate_key = build_object_key(directive.api_path, content_hash, payload.media_type, generation=uuid4().hex)
        try:
            # Bound queueing, locking, upload and commit together. The upload retains
            # its own smaller I/O budget; cancellation rolls the transaction back.
            budget = min(240.0, float(self._settings.upload_timeout_seconds) + 3 * self._command_timeout())
            async with asyncio.timeout(budget):
                # This separate committed intent must precede the writer transaction:
                # acquiring another connection while holding its lock can exhaust the pool.
                started = self._clock()
                prepared = await asyncio.wait_for(
                    self._index.prepare_upload(content_hash, candidate_key), self._command_timeout()
                )
                self._stage_done(stages, "index_prepare", started)
                if prepared:
                    for name, elapsed in prepared.items():
                        self._stage_value(stages, name, elapsed)
                async with self._index.content_writer(content_hash) as writer:
                    self._stage_value(stages, "index_acquire", getattr(writer, "acquire_seconds", 0.0))
                    self._stage_value(stages, "index_connect", getattr(writer, "connect_seconds", 0.0))
                    self._stage_value(stages, "index_lock", getattr(writer, "lock_seconds", 0.0))
                    outcome = await self._process_locked(
                        payload, directive, content_hash, candidate_key, writer, stages
                    )
                    if outcome.ref is not None:
                        await asyncio.wait_for(writer.finish_upload(candidate_key), self._command_timeout())
            if outcome.ref is not None:
                self._stats.incr("reused" if outcome.ref.reused else "published")
                if outcome.ref.index_written:
                    self._stats.incr("index_writes")
            return outcome
        except (IndexUnavailable, IndexSchemaError, TimeoutError) as exc:
            self._mark_index_unusable("content_writer", exc)
            self._stats.record_error("content_writer", exc)
            return self._degraded("index_unavailable", stages)

    async def _process_locked(
        self,
        payload: EncodedImagePayload,
        directive: RenderCacheDirective,
        content_hash: str,
        candidate_key: str,
        writer: ContentWriter,
        stages: dict[str, float],
    ) -> ArtifactOutcome:
        store = self._store
        assert store is not None
        row = await self._lookup(writer, content_hash, stages)
        if self._index_state() != "usable":
            return self._degraded("index_unavailable", stages)

        reused = row is not None and row.storage_backend == STORAGE_BACKEND_GARAGE
        if reused:
            assert row is not None
            cdn_path = row.cdn_path
            media_type = row.media_type or payload.media_type
            size_bytes = row.size_bytes if row.size_bytes is not None else len(payload.image_bytes)
            upload_elapsed = 0.0
            if is_foreign_cdn_path(cdn_path):
                self._stats.incr("reused_foreign")
        else:
            cdn_path = candidate_key
            if not await asyncio.wait_for(writer.upload_prepared(cdn_path), self._command_timeout()):
                raise IndexUnavailable("upload intent expired or already claimed by GC")
            media_type = payload.media_type
            size_bytes = len(payload.image_bytes)
            uploaded = await self._upload(store, cdn_path, payload, stages)
            if isinstance(uploaded, str):
                return self._degraded(uploaded, stages)  # A3: no index write after a failed upload
            upload_elapsed = uploaded

        expires = expires_at_for(self._now(), directive.ttl_seconds)
        index_written = False
        if directive.store:
            state = self._index_state()
            if state == "usable":
                content = ContentRow(
                    hash=content_hash,
                    group_name=directive.group,
                    cdn_path=cdn_path,
                    storage_backend=STORAGE_BACKEND_GARAGE,
                    media_type=media_type,
                    size_bytes=size_bytes,
                    expires_at=expires,
                    writer_node=row.writer_node if reused else self._node_name,
                    written_at=row.written_at if reused else self._now(),
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
                index_written = await self._record(writer, content, request, stages)
                if not index_written:
                    return self._degraded("index_unavailable", stages)
            else:
                self._stats.index_skipped(state)
                return self._degraded("index_unavailable", stages)

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
            upload_elapsed=upload_elapsed,
            node_name=(
                row.writer_node
                if reused
                and row.writer_node
                and row.written_at
                and 0 <= (self._now() - row.written_at).total_seconds() <= 120
                else self._node_name
            ),
        )
        return ArtifactOutcome(ref=ref, degraded=False, reason="reused" if reused else "ok", stages=stages)
