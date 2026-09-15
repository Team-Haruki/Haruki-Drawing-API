"""`ArtifactRef`: the JSON document Drawing returns instead of image bytes in artifact mode (plan §8.3).

Key derivation on a miss is frozen and caller-independent (C5): `pjsk/<api_path>/<sha256>.<ext>`. The first
segment is the literal `OBJECT_KEY_PREFIX`, never the cache group, and the provider `root` is never joined in.
On a reuse the stored row's `cdn_path` is returned verbatim (addendum A2), so a ref may carry a path whose
`api_path` segment differs from the request's, or Cloud's own `pjsk/<sha256>.<ext>` shape. Ops tooling that
must target only Drawing-written artifacts uses the prefix `pjsk/api/`, never `pjsk/`.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from typing import Any

OBJECT_KEY_PREFIX = "pjsk"  # C5, FROZEN — not a setting, not caller-controlled
DRAWING_KEY_PREFIX = f"{OBJECT_KEY_PREFIX}/api/"  # what Drawing-written keys start with (Cloud routes are /api/...)
ARTIFACT_KIND = "artifact_ref"
STORAGE_BACKEND_GARAGE = "garage"
_EXT_BY_MEDIA_TYPE = {"image/png": "png", "image/jpeg": "jpg"}


class UnsupportedMediaType(ValueError):
    """The payload's media type has no artifact extension (only PNG and JPEG are published)."""


def extension_for_media_type(media_type: str | None) -> str | None:
    """`png` / `jpg` for the two published media types, else `None`."""
    if not media_type:
        return None
    return _EXT_BY_MEDIA_TYPE.get(media_type.split(";", 1)[0].strip().lower())


def build_object_key(api_path: str, content_hash: str, media_type: str) -> str:
    """The C5 key for a freshly uploaded artifact: `pjsk/<api_path>/<sha256>.<ext>`."""
    ext = extension_for_media_type(media_type)
    if ext is None:
        raise UnsupportedMediaType(media_type)
    path = api_path.strip("/")
    return f"{OBJECT_KEY_PREFIX}/{path}/{content_hash}.{ext}"


def is_foreign_cdn_path(cdn_path: str) -> bool:
    """True when a stored path was not written by Drawing (e.g. Cloud's `storeHashed` `pjsk/<sha256>.<ext>`)."""
    return not cdn_path.startswith(DRAWING_KEY_PREFIX)


def expires_at_for(now: datetime, ttl_seconds: int) -> datetime | None:
    """`now + ttl` in UTC, or `None` for an infinite TTL (`0`)."""
    if ttl_seconds <= 0:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return (now + timedelta(seconds=ttl_seconds)).astimezone(UTC)


def format_rfc3339(value: datetime | None) -> str | None:
    """RFC3339 UTC with a `Z` suffix and second precision; `None` stays `None` (JSON `null`)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    kind: str
    hash: str
    cdn_path: str
    storage_backend: str
    bucket: str
    object_key: str
    size_bytes: int
    media_type: str
    width: int | None
    height: int | None
    cache_key: str
    ttl_seconds: int
    expires_at: str | None
    reused: bool
    index_written: bool
    upload_elapsed: float
    node_name: str

    def to_json(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


REF_FIELDS: tuple[str, ...] = tuple(field.name for field in fields(ArtifactRef))
