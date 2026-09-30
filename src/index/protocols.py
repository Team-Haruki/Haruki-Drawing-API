"""The render-index protocol and its row types (plan §9.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
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

    `cdn_path`, `media_type`, `size_bytes`, `writer_node` and `written_at` are the stored row after the upsert:
    for a hash that already had a `garage` row they are that row's own values, which are authoritative
    (addendum A2). `prior_backend` is the row's `storage_backend` before the write (`None` for a new hash). The
    timings are the pool acquire, the `BEGIN` + hash-lock round trip, and, on the first write of an event loop,
    the pool creation plus preflight (`0.0` otherwise).
    """

    cdn_path: str
    media_type: str | None
    size_bytes: int | None
    prior_backend: str | None
    acquire_seconds: float = 0.0
    connect_seconds: float = 0.0
    writer_node: str | None = None
    written_at: datetime | None = None
    lock_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class PreparedUpload:
    """What `prepare_upload` found: the path of an existing `garage` row (no intent was queued, skip the PUT),
    or `None` (the candidate's cleanup intent is committed and the PUT may start)."""

    existing_path: str | None
    acquire_seconds: float = 0.0
    connect_seconds: float = 0.0


class IndexUnavailable(Exception):  # names fixed by the plan (§9.1)
    """Connect / timeout / transport failure, or a backoff window after one."""


class IndexContention(IndexUnavailable):
    """A healthy database could not grant a content lock within this request budget."""


class IndexSchemaError(Exception):
    """Cloud's schema migration for the render index has not shipped yet."""


class IndexWriteFailed(IndexUnavailable):
    """`record` did not commit."""


@runtime_checkable
class RenderIndex(Protocol):
    async def prepare_upload(self, content_hash: str, cdn_path: str) -> PreparedUpload: ...

    # Lock, ownership-gated RECORD and commit; None when no index row was written.
    async def record_upload(
        self, content: ContentRow, request: RequestRow, *, uploaded: bool
    ) -> RecordResult | None: ...

    async def preflight(self) -> None: ...  # raises IndexSchemaError / IndexUnavailable

    async def record(self, content: ContentRow, request: RequestRow) -> RecordResult: ...  # ONE statement

    async def close(self) -> None: ...
