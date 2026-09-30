"""The render-index protocol and its row types (plan §9.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from contextlib import AbstractAsyncContextManager
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class ContentRow:
    """One `image_cache_entries` row as Drawing writes it.

    There is deliberately no `bucket` field: the table has no bucket column and the programme has exactly one
    image-cache bucket. `last_referenced_at` is written but never read back by Drawing, so it is not part of
    this row.
    """

    hash: str
    group_name: str
    cdn_path: str
    storage_backend: str
    media_type: str | None
    size_bytes: int | None
    expires_at: datetime | None
    writer_node: str | None = None
    written_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RequestRow:
    """One `render_cache_index` row (every column is inserted explicitly, addendum A5)."""

    request_key: str
    content_hash: str
    api_path: str
    user_id: str
    group_name: str
    key_version: int
    ttl_seconds: int
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class RecordResult:
    """What the one-statement index write reports back.

    `cdn_path`, `media_type` and `size_bytes` are the stored row after the upsert: for a hash that already had a
    `garage` row they are that row's own values, which are authoritative (addendum A2). `prior_backend` is the
    row's `storage_backend` before the write (`None` for a new hash). The timings are the pool acquire and, on
    the first write of an event loop, the pool creation plus preflight (`0.0` otherwise).
    """

    cdn_path: str
    media_type: str | None
    size_bytes: int | None
    prior_backend: str | None
    acquire_seconds: float = 0.0
    connect_seconds: float = 0.0


class IndexUnavailable(Exception):  # names fixed by the plan (§9.1)
    """Connect / timeout / transport failure, or a backoff window after one."""


class IndexSchemaError(Exception):
    """Cloud's schema migration for the render index has not shipped yet."""


class IndexWriteFailed(IndexUnavailable):
    """`record` did not commit."""


class ContentWriter(Protocol):
    async def upload_prepared(self, cdn_path: str) -> bool: ...

    async def lookup_content(self, content_hash: str) -> ContentRow | None: ...

    async def record(self, content: ContentRow, request: RequestRow) -> RecordResult: ...

    async def finish_upload(self, cdn_path: str) -> None: ...


@runtime_checkable
class RenderIndex(Protocol):
    async def prepare_upload(self, content_hash: str, cdn_path: str) -> dict[str, float] | None: ...

    def content_writer(self, content_hash: str) -> AbstractAsyncContextManager[ContentWriter]: ...

    async def preflight(self) -> None: ...  # raises IndexSchemaError / IndexUnavailable

    async def record(self, content: ContentRow, request: RequestRow) -> RecordResult: ...  # ONE statement

    async def close(self) -> None: ...
