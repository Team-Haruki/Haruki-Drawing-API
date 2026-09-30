"""In-memory doubles for the storage layer. Plain classes: no `opendal`, no `asyncpg`.

Fakes live in `tests/`, never in `src/` (coverage `source = ["src"]`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import time
from typing import Any

from src.index.protocols import ContentRow, PreparedUpload, RecordResult, RequestRow
from src.index.sql import RECORD, RECORD_UPLOAD
from src.storage.protocols import ObjectStat, StorageNotFound, StorageTooLarge, StorageUnavailable, validate_object_key


class FakeObjectStore:
    """Implements `ObjectStore` over a dict.

    Failure injection is active when any of `fail`, `fail_keys`, `fail_after` is set: the first `fail_after`
    operations succeed, `fail_keys` limits the failure to those keys, and the raised exception is `fail`
    (default `StorageUnavailable`). `delay` sleeps on the loop before each operation.
    """

    def __init__(
        self,
        objects: dict[str, bytes] | None = None,
        *,
        last_modified: dict[str, int] | None = None,
        fail: Exception | None = None,
        fail_keys: set[str] | None = None,
        fail_after: int | None = None,
        delay: float = 0.0,
        name: str = "fake",
        bucket: str = "fake-bucket",
        max_read_bytes: int | None = None,
    ) -> None:
        self.name = name
        self.bucket = bucket
        self.objects: dict[str, bytes] = dict(objects or {})
        self.last_modified: dict[str, int] = dict(last_modified or {})
        self.content_types: dict[str, str] = {}
        self.fail = fail
        self.fail_keys = set(fail_keys) if fail_keys is not None else None
        self.fail_after = fail_after
        self.delay = delay
        self.max_read_bytes = max_read_bytes
        self.ops = 0
        self.reads: list[str] = []
        self.stats: list[str] = []
        self.writes: list[tuple[str, int, str]] = []
        self.closed = False

    async def _enter(self, key: str) -> None:
        validate_object_key(key)
        if self.delay:
            await asyncio.sleep(self.delay)
        self.ops += 1
        if self.fail is None and self.fail_keys is None and self.fail_after is None:
            return
        if self.fail_after is not None and self.ops <= self.fail_after:
            return
        if self.fail_keys is not None and key not in self.fail_keys:
            return
        raise self.fail or StorageUnavailable(f"{self.name}: injected failure for {key}")

    def _stat(self, key: str) -> ObjectStat | None:
        data = self.objects.get(key)
        if data is None:
            return None
        return ObjectStat(
            size=len(data),
            last_modified_ns=self.last_modified.get(key),
            etag=None,
            content_type=self.content_types.get(key),
        )

    async def read(self, key: str, *, max_bytes: int | None = None) -> tuple[bytes, ObjectStat]:
        await self._enter(key)
        self.reads.append(key)
        stat = self._stat(key)
        if stat is None:
            raise StorageNotFound(f"{self.name}: object not found: {key}")
        limit = max_bytes if max_bytes is not None else self.max_read_bytes
        if limit is not None and stat.size > limit:
            raise StorageTooLarge(f"{self.name}: {key} is {stat.size} bytes, cap {limit}")
        return self.objects[key], stat

    async def stat(self, key: str) -> ObjectStat | None:
        await self._enter(key)
        self.stats.append(key)
        return self._stat(key)

    async def write(self, key: str, data: bytes | memoryview, *, content_type: str) -> None:
        await self._enter(key)
        payload = bytes(data)
        self.objects[key] = payload
        self.content_types[key] = content_type
        self.last_modified[key] = time.time_ns()
        self.writes.append((key, len(payload), content_type))

    async def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------------------------- render index


class UndefinedTableError(Exception):
    """Stand-in for `asyncpg.exceptions.UndefinedTableError`, matched by class name."""


class UndefinedColumnError(Exception):
    """Stand-in for `asyncpg.exceptions.UndefinedColumnError`, matched by class name."""


class FakeRenderIndex:
    """Implements `RenderIndex` over dicts of content rows and intents (the `RECORD` / `RECORD_UPLOAD` rules).

    `lookup_error` fails `prepare_upload` (the lookup + intent round trip), `record_error` fails the writes.
    `expired_intents` holds (hash, path) intents GC may already have claimed: `record_upload` will not use them,
    and an unrecorded uploaded path is (re-)queued in `intents`. Every call is recorded in `calls`; the write
    transaction's lock/commit/rollback in `writer_events`.
    """

    def __init__(
        self,
        content: dict[str, ContentRow] | None = None,
        *,
        preflight_error: Exception | None = None,
        lookup_error: Exception | None = None,
        record_error: Exception | None = None,
        acquire_seconds: float = 0.0,
        connect_seconds: float = 0.0,
    ) -> None:
        self.content: dict[str, ContentRow] = dict(content or {})
        self.requests: dict[str, RequestRow] = {}
        self.lookup_error = lookup_error
        self.preflight_error = preflight_error
        self.record_error = record_error
        self.acquire_seconds = acquire_seconds
        self.connect_seconds = connect_seconds
        self.calls: list[tuple[str, object]] = []
        self.closed = False
        self.writer_events: list[str] = []
        self.intents: set[tuple[str, str]] = set()
        self.expired_intents: set[tuple[str, str]] = set()

    async def prepare_upload(self, content_hash: str, cdn_path: str) -> PreparedUpload:
        self.calls.append(("prepare_upload", content_hash))
        self.writer_events.append("prepare_upload")
        if self.lookup_error is not None:
            raise self.lookup_error
        existing = self.content.get(content_hash)
        if existing is not None and existing.storage_backend == "garage":
            return PreparedUpload(existing.cdn_path, self.acquire_seconds, self.connect_seconds)
        self.intents.add((content_hash, cdn_path))
        return PreparedUpload(None, self.acquire_seconds, self.connect_seconds)

    async def record_upload(self, content: ContentRow, request: RequestRow, *, uploaded: bool) -> RecordResult | None:
        self.calls.append(("record_upload", (content, request, uploaded)))
        self.writer_events.append("lock")
        if self.record_error is not None:
            self.writer_events.append("rollback")
            raise self.record_error
        intent = (content.hash, content.cdn_path)
        existing = self.content.get(content.hash)
        owned = intent in self.intents and intent not in self.expired_intents
        result = None
        if owned or (existing is not None and existing.storage_backend == "garage"):
            result = self._upsert(content, request)
        if result is not None and result.cdn_path == content.cdn_path:
            self.intents.discard(intent)
        elif uploaded:
            self.intents.add(intent)  # an unrecorded upload always ends up queued for GC
        self.writer_events.append("commit")
        return result

    async def preflight(self) -> None:
        self.calls.append(("preflight", None))
        if self.preflight_error is not None:
            raise self.preflight_error

    async def lookup_content(self, content_hash: str) -> ContentRow | None:
        self.calls.append(("lookup_content", content_hash))
        if self.lookup_error is not None:
            raise self.lookup_error
        return self.content.get(content_hash)

    async def record(self, content: ContentRow, request: RequestRow) -> RecordResult:
        self.calls.append(("record", (content, request)))
        if self.record_error is not None:
            raise self.record_error
        return self._upsert(content, request)

    def _upsert(self, content: ContentRow, request: RequestRow) -> RecordResult:
        existing = self.content.get(content.hash)
        if existing is None or existing.storage_backend != "garage":
            self.content[content.hash] = content
        self.requests[request.request_key] = request
        stored = self.content[content.hash]
        return RecordResult(
            cdn_path=stored.cdn_path,
            media_type=stored.media_type,
            size_bytes=stored.size_bytes,
            prior_backend=existing.storage_backend if existing is not None else None,
            acquire_seconds=self.acquire_seconds,
            connect_seconds=self.connect_seconds,
            writer_node=stored.writer_node,
            written_at=stored.written_at,
        )

    async def close(self) -> None:
        self.calls.append(("close", None))
        self.closed = True


class _AsyncContext:
    def __init__(self, value: Any, on_exit: Callable[[BaseException | None], None] | None = None) -> None:
        self._value = value
        self._on_exit = on_exit

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, exc_type: object, exc: BaseException | None, tb: object) -> bool:
        if self._on_exit is not None:
            self._on_exit(exc)
        return False


class FakeConn:
    """Duck-typed asyncpg connection: `execute`, `fetchrow`, `fetchval`, `transaction()`; records SQL and args.

    `rows` maps the first positional argument of `fetchrow` to the returned row (a dict); an unknown key returns
    `default_row`, and when that is None a `RECORD` / `RECORD_UPLOAD` echoes its content arguments as the stored
    row (a new hash) unless `echo_records` is off. `values` maps an SQL text to what `fetchval` returns (default None).
    `errors` maps an exact SQL text to the exception raised when that statement runs.
    """

    def __init__(self, pool: FakePgPool) -> None:
        self._pool = pool

    async def execute(self, sql: str, *args: Any) -> str:
        self._pool.log("execute", sql, args)
        return "OK"

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self._pool.log("fetchrow", sql, args)
        row = self._pool.rows.get(args[0], self._pool.default_row) if args else None
        if row is None and self._pool.echo_records and sql in (RECORD, RECORD_UPLOAD):
            keys = ("cdn_path", "size_bytes", "media_type", "writer_node", "written_at")
            row = dict(zip(keys, (args[2], args[3], args[4], args[6], args[7]), strict=True))
            row["prior_backend"] = None
        return row

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self._pool.log("fetchval", sql, args)
        return self._pool.values.get(sql)

    def transaction(self) -> _AsyncContext:
        self._pool.events.append(("transaction.begin", None, ()))

        def _end(exc: BaseException | None) -> None:
            self._pool.events.append(("transaction.rollback" if exc else "transaction.commit", None, ()))

        return _AsyncContext(None, _end)


class FakePgPool:
    """Duck-typed asyncpg pool: `acquire()` yields a `FakeConn`; `events` is the ordered call log."""

    def __init__(
        self,
        *,
        rows: dict[str, dict[str, Any]] | None = None,
        default_row: dict[str, Any] | None = None,
        values: dict[str, Any] | None = None,
        echo_records: bool = True,
        errors: dict[str, Exception] | None = None,
        acquire_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.rows: dict[str, dict[str, Any]] = dict(rows or {})
        self.default_row = default_row
        self.values: dict[str, Any] = dict(values or {})
        self.echo_records = echo_records
        self.errors: dict[str, Exception] = dict(errors or {})
        self.acquire_error = acquire_error
        self.close_error = close_error
        self.events: list[tuple[str, str | None, tuple[Any, ...]]] = []
        self.acquire_kwargs: list[dict[str, Any]] = []
        self.closed = False

    def log(self, kind: str, sql: str, args: tuple[Any, ...]) -> None:
        self.events.append((kind, sql, args))
        error = self.errors.get(sql)
        if error is not None:
            raise error

    def statements(self) -> list[str]:
        return [sql for kind, sql, _ in self.events if sql is not None and kind in ("execute", "fetchrow", "fetchval")]

    @property
    def round_trips(self) -> int:
        return len(self.statements())

    def acquire(self, **kwargs: Any) -> _AsyncContext:
        self.acquire_kwargs.append(kwargs)
        if self.acquire_error is not None:
            raise self.acquire_error
        return _AsyncContext(FakeConn(self))

    async def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


# ---------------------------------------------------------------------------------------------- artifact runtime


def build_test_runtime(
    *,
    store: Any = None,
    index: Any = None,
    settings: Any = None,
    node_name: str = "test-node",
    stats: Any = None,
    **service_kwargs: Any,
) -> Any:
    """An enabled `ArtifactRuntime` over fakes (a fresh `FakeObjectStore` when `store` is None)."""
    from src.artifact.runtime import ArtifactRuntime
    from src.artifact.service import ArtifactService
    from src.artifact.stats import artifact_stats
    from src.settings import StorageSettings

    storage = settings if settings is not None else StorageSettings(enabled=True)
    store = store if store is not None else FakeObjectStore(bucket="image-cache")
    stats = stats if stats is not None else artifact_stats
    service = ArtifactService(
        store=store,
        index=index,
        settings=storage,
        node_name=node_name,
        stats=stats,
        **service_kwargs,
    )
    stats.set_runtime_state(enabled=True, bucket=store.bucket)
    return ArtifactRuntime(service=service, store=store, index=service.index, stats=stats)
