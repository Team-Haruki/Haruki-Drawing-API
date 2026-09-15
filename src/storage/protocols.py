"""The object-store protocol shared by the asset mirror and the artifact output.

Four verbs only: `read`, `stat`, `write`, `close`. There is deliberately no `delete` (C6: GC belongs to
Haruki-Cloud) and no `list` (E6: buckets are never listed; the mirror sweeper walks the local directory).
`stat` returning `None` replaces an `exists` verb.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ObjectRef:
    bucket: str
    key: str  # relative, forward-slash, no leading "/", no "." / ".." parts


@dataclass(frozen=True, slots=True)
class ObjectStat:
    size: int
    last_modified_ns: int | None  # feeds os.utime so every Drawing node agrees on (mtime_ns, size) identity
    etag: str | None
    content_type: str | None


class StorageError(Exception):
    """Base class of every object-store failure."""


class StorageNotFound(StorageError):
    """The object does not exist."""


class StorageUnavailable(StorageError):
    """Connect / timeout / 5xx after retries — callers take the degraded path."""


class StorageWriteFailed(StorageUnavailable):
    """A PUT did not reach write quorum (addendum A3)."""


class StorageTooLarge(StorageError):
    """The object is larger than the caller's read cap."""


def validate_object_key(key: str) -> str:
    """Return `key` unchanged when it is a safe relative object key, else raise `ValueError`."""
    if not isinstance(key, str) or not key:
        raise ValueError("object key must be a non-empty string")
    if key.startswith("/") or "\\" in key or "\x00" in key:
        raise ValueError(f"invalid object key: {key!r}")
    for part in key.split("/"):
        if part in ("", ".", ".."):
            raise ValueError(f"invalid object key: {key!r}")
    return key


@runtime_checkable
class ObjectStore(Protocol):
    name: str
    bucket: str

    async def read(self, key: str, *, max_bytes: int | None = None) -> tuple[bytes, ObjectStat]: ...

    async def stat(self, key: str) -> ObjectStat | None: ...

    async def write(self, key: str, data: bytes | memoryview, *, content_type: str) -> None: ...

    async def close(self) -> None: ...
