"""The render-index protocol and its row types (plan §9.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class ContentRow:
    """One `image_cache_entries` row as Drawing reads and writes it.

    `cdn_path` is authoritative for a reused artifact (addendum A2). There is deliberately no `bucket` field:
    the table has no bucket column and the programme has exactly one image-cache bucket.
    `last_referenced_at` is written but never read back by Drawing, so it is not part of this row.
    """

    hash: str
    group_name: str
    cdn_path: str
    storage_backend: str
    media_type: str | None
    size_bytes: int | None
    expires_at: datetime | None


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


class IndexUnavailable(Exception):  # names fixed by the plan (§9.1)
    """Connect / timeout / transport failure, or a backoff window after one."""


class IndexSchemaError(Exception):
    """Cloud's schema migration for the render index has not shipped yet."""


class IndexWriteFailed(IndexUnavailable):
    """`record` did not commit."""


@runtime_checkable
class RenderIndex(Protocol):
    async def preflight(self) -> None: ...  # raises IndexSchemaError / IndexUnavailable

    async def lookup_content(self, content_hash: str) -> ContentRow | None: ...

    async def record(self, content: ContentRow, request: RequestRow) -> None: ...  # ONE transaction

    async def close(self) -> None: ...
