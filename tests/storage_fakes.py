"""In-memory doubles for the storage layer. Plain classes: no `opendal`, no `asyncpg`.

Fakes live in `tests/`, never in `src/` (coverage `source = ["src"]`).
"""

from __future__ import annotations

import asyncio
import time

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
