"""In-memory doubles for the storage layer. Plain classes: no `opendal`, no `asyncpg`.

Fakes live in `tests/`, never in `src/` (coverage `source = ["src"]`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import time
from typing import Any

from src.index.protocols import ContentRow, RequestRow
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
    """Implements `RenderIndex` over a dict of content rows; records every call."""

    def __init__(
        self,
        content: dict[str, ContentRow] | None = None,
        *,
        preflight_error: Exception | None = None,
        lookup_error: Exception | None = None,
        record_error: Exception | None = None,
    ) -> None:
        self.content: dict[str, ContentRow] = dict(content or {})
        self.requests: dict[str, RequestRow] = {}
        self.preflight_error = preflight_error
        self.lookup_error = lookup_error
        self.record_error = record_error
        self.calls: list[tuple[str, object]] = []
        self.closed = False

    async def preflight(self) -> None:
        self.calls.append(("preflight", None))
        if self.preflight_error is not None:
            raise self.preflight_error

    async def lookup_content(self, content_hash: str) -> ContentRow | None:
        self.calls.append(("lookup_content", content_hash))
        if self.lookup_error is not None:
            raise self.lookup_error
        return self.content.get(content_hash)

    async def record(self, content: ContentRow, request: RequestRow) -> None:
        self.calls.append(("record", (content, request)))
        if self.record_error is not None:
            raise self.record_error
        existing = self.content.get(content.hash)
        if existing is None or existing.storage_backend != "garage":
            self.content[content.hash] = content
        self.requests[request.request_key] = request

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
    """Duck-typed asyncpg connection: `execute`, `fetchrow`, `transaction()`; records SQL text and args.

    `rows` maps the first positional argument of `fetchrow` to the returned row (a dict). `errors` maps an
    exact SQL text to the exception raised when that statement runs.
    """

    def __init__(self, pool: FakePgPool) -> None:
        self._pool = pool

    async def execute(self, sql: str, *args: Any) -> str:
        self._pool.log("execute", sql, args)
        return "OK"

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self._pool.log("fetchrow", sql, args)
        return self._pool.rows.get(args[0]) if args else None

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
        errors: dict[str, Exception] | None = None,
        acquire_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.rows: dict[str, dict[str, Any]] = dict(rows or {})
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
        return [sql for kind, sql, _ in self.events if sql is not None and kind in ("execute", "fetchrow")]

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
