"""`ObjectStore` over an `opendal.AsyncOperator`. `opendal` is imported lazily inside `from_provider`."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING, Any

from src.storage.protocols import (
    ObjectStat,
    StorageError,
    StorageNotFound,
    StorageTooLarge,
    StorageUnavailable,
    StorageWriteFailed,
    validate_object_key,
)
from src.storage.provider import opendal_kwargs, resolve_template

if TYPE_CHECKING:  # pragma: no cover
    from src.settings import StorageProviderSettings

logger = logging.getLogger("src.storage.opendal_store")

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _is_opendal_error(exc: BaseException) -> bool:
    return type(exc).__module__.split(".", 1)[0] == "opendal"


def _to_ns(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        delta = value - _EPOCH
        return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000
    if isinstance(value, int):
        return value
    return None


def _stat_from_metadata(meta: Any) -> ObjectStat:
    return ObjectStat(
        size=int(getattr(meta, "content_length", 0) or 0),
        last_modified_ns=_to_ns(getattr(meta, "last_modified", None)),
        etag=getattr(meta, "etag", None),
        content_type=getattr(meta, "content_type", None),
    )


class OpendalObjectStore:
    """Async object store; every method must run inside a running event loop (pyo3-async-runtimes)."""

    def __init__(self, operator: Any, *, name: str, bucket: str, max_read_bytes: int | None = None) -> None:
        self._op = operator
        self.name = name
        self.bucket = bucket
        self.max_read_bytes = max_read_bytes
        self.closed = False

    @classmethod
    def from_provider(
        cls,
        p: StorageProviderSettings,
        *,
        region: str | None,
        timeout: float,
        io_timeout: float,
        retries: int,
        concurrency: int,
        name: str,
        max_read_bytes: int | None = None,
    ) -> OpendalObjectStore:
        import opendal  # lazy — never at module import
        from opendal.layers import ConcurrentLimitLayer, RetryLayer, TimeoutLayer

        op = opendal.AsyncOperator(p.scheme, **opendal_kwargs(p, region))
        # `op.layer(A).layer(B)` makes B the OUTER layer: Timeout wraps Retry, so the timeout budget caps the
        # whole retry loop, and ConcurrentLimit wraps both.
        op = (
            op.layer(RetryLayer(max_times=retries, jitter=True))
            .layer(TimeoutLayer(timeout=timeout, io_timeout=io_timeout))
            .layer(ConcurrentLimitLayer(concurrency))
        )
        return cls(op, name=name, bucket=resolve_template(p.bucket, region), max_read_bytes=max_read_bytes)

    def _translate(self, exc: BaseException, key: str, *, write: bool) -> StorageError:
        if _is_opendal_error(exc) and type(exc).__name__ == "NotFound":
            return StorageNotFound(f"{self.name}: object not found: {key}")
        kind = StorageWriteFailed if write else StorageUnavailable
        return kind(f"{self.name}: {'write' if write else 'read'} failed for {key}: {type(exc).__name__}: {exc}")

    @staticmethod
    def _translatable(exc: BaseException) -> bool:
        return _is_opendal_error(exc) or isinstance(exc, (TimeoutError, asyncio.TimeoutError, OSError))

    async def stat(self, key: str) -> ObjectStat | None:
        validate_object_key(key)
        try:
            meta = await self._op.stat(key)
        except Exception as exc:
            if not self._translatable(exc):
                raise
            err = self._translate(exc, key, write=False)
            if isinstance(err, StorageNotFound):
                return None
            raise err from exc
        return _stat_from_metadata(meta)

    async def read(self, key: str, *, max_bytes: int | None = None) -> tuple[bytes, ObjectStat]:
        validate_object_key(key)
        limit = max_bytes if max_bytes is not None else self.max_read_bytes
        stat = await self.stat(key)
        if stat is None:
            raise StorageNotFound(f"{self.name}: object not found: {key}")
        if limit is not None and stat.size > limit:
            raise StorageTooLarge(f"{self.name}: {key} is {stat.size} bytes, cap {limit}")
        try:
            data = await self._op.read(key)
        except Exception as exc:
            if not self._translatable(exc):
                raise
            raise self._translate(exc, key, write=False) from exc
        data = bytes(data)
        if limit is not None and len(data) > limit:
            raise StorageTooLarge(f"{self.name}: {key} is {len(data)} bytes, cap {limit}")
        if stat.size != len(data):
            stat = ObjectStat(len(data), stat.last_modified_ns, stat.etag, stat.content_type)
        return data, stat

    async def write(self, key: str, data: bytes | memoryview, *, content_type: str) -> None:
        validate_object_key(key)
        payload = data.tobytes() if isinstance(data, memoryview) else data
        try:
            await self._op.write(key, payload, content_type=content_type)
        except Exception as exc:
            if not self._translatable(exc):
                raise
            raise self._translate(exc, key, write=True) from exc

    async def close(self) -> None:
        # AsyncOperator has no close(); dropping the reference releases the underlying client.
        self.closed = True
