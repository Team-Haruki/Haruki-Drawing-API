"""ArtifactRef shape and C5 key derivation (plan §8.3)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
import hashlib
import json

import pytest

from src.artifact import ref as ref_mod
from src.artifact.directive import RenderCacheDirective
from src.artifact.ref import (
    OBJECT_KEY_PREFIX,
    REF_FIELDS,
    ArtifactRef,
    UnsupportedMediaType,
    build_object_key,
    expires_at_for,
    extension_for_media_type,
    format_rfc3339,
    is_foreign_cdn_path,
)
from src.artifact.service import ArtifactService
from src.artifact.stats import ArtifactStats
from src.core.image_payload import EncodedImagePayload
from src.settings import StorageSettings
from tests.storage_fakes import FakeObjectStore

BRIEF_FIELDS = {
    "kind",
    "hash",
    "cdn_path",
    "storage_backend",
    "bucket",
    "object_key",
    "size_bytes",
    "media_type",
    "width",
    "height",
    "cache_key",
    "ttl_seconds",
    "expires_at",
    "reused",
    "index_written",
    "upload_elapsed",
    "node_name",
}


def _ref(**overrides) -> ArtifactRef:
    values = {
        "kind": "artifact_ref",
        "hash": "ab" * 32,
        "cdn_path": "pjsk/api/x/" + "ab" * 32 + ".png",
        "storage_backend": "garage",
        "bucket": "image-cache",
        "object_key": "pjsk/api/x/" + "ab" * 32 + ".png",
        "size_bytes": 3,
        "media_type": "image/png",
        "width": None,
        "height": None,
        "cache_key": "0123456789abcdef",
        "ttl_seconds": 0,
        "expires_at": None,
        "reused": False,
        "index_written": False,
        "upload_elapsed": 0.0,
        "node_name": "cn09",
    }
    values.update(overrides)
    return ArtifactRef(**values)


def test_json_key_set_is_exactly_the_brief_fields() -> None:
    body = _ref().to_json()
    assert set(body) == BRIEF_FIELDS
    assert len(body) == 17
    assert "node_name" in body
    assert "node" not in body
    assert set(REF_FIELDS) == BRIEF_FIELDS


def test_nullable_fields_serialise_as_json_null() -> None:
    text = json.dumps(_ref().to_json())
    decoded = json.loads(text)
    assert decoded["width"] is None
    assert decoded["height"] is None
    assert decoded["expires_at"] is None


def test_object_key_prefix_is_frozen_literal() -> None:
    assert OBJECT_KEY_PREFIX == "pjsk"
    assert (
        build_object_key("/api/pjsk/card/detail", "f" * 64, "image/png") == f"pjsk/api/pjsk/card/detail/{'f' * 64}.png"
    )
    assert build_object_key("api/x", "a" * 64, "image/jpeg; charset=binary") == f"pjsk/api/x/{'a' * 64}.jpg"


@pytest.mark.parametrize(
    ("media", "ext"),
    [("image/png", "png"), ("image/jpeg", "jpg"), ("IMAGE/PNG", "png"), ("image/webp", None), ("", None), (None, None)],
)
def test_extension_from_media_type(media, ext) -> None:
    assert extension_for_media_type(media) == ext


def test_unsupported_media_raises() -> None:
    with pytest.raises(UnsupportedMediaType):
        build_object_key("api/x", "a" * 64, "raw_rgba_premul")


def test_foreign_path_detection() -> None:
    assert not is_foreign_cdn_path("pjsk/api/card/" + "a" * 64 + ".png")
    assert is_foreign_cdn_path("pjsk/" + "a" * 64 + ".png")


def test_expires_at_helpers() -> None:
    now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)
    assert expires_at_for(now, 0) is None
    assert expires_at_for(now, 60) == now + timedelta(seconds=60)
    naive = datetime(2026, 9, 13, 12, 0, 0)
    assert expires_at_for(naive, 1) == now + timedelta(seconds=1)
    assert format_rfc3339(None) is None
    assert format_rfc3339(now) == "2026-09-13T12:00:00Z"
    tokyo = timezone(timedelta(hours=9))
    assert format_rfc3339(datetime(2026, 9, 13, 21, 0, 0, tzinfo=tokyo)) == "2026-09-13T12:00:00Z"
    assert format_rfc3339(naive) == "2026-09-13T12:00:00Z"


def _directive(**overrides) -> RenderCacheDirective:
    values = {
        "cache_key": "0123456789abcdef",
        "key_version": 3,
        "ttl_seconds": 0,
        "store": True,
        "group": "custom-group",
        "api_path": "api/pjsk/honor",
        "user_id": "public",
    }
    values.update(overrides)
    return RenderCacheDirective(**values)


def test_fresh_key_ignores_group_and_non_empty_root() -> None:
    data = b"\x89PNG-bytes"
    digest = hashlib.sha256(data).hexdigest()
    settings = StorageSettings(enabled=True)
    settings.provider.root = "some/root"
    store = FakeObjectStore(bucket="image-cache")
    service = ArtifactService(
        store=store,
        index=None,
        settings=settings,
        node_name="cn09",
        stats=ArtifactStats(),
        now=lambda: datetime(2026, 9, 13, tzinfo=UTC),
    )
    payload = EncodedImagePayload(data, "image/png", "x.png", 10, None, "RGBA", 0.0)
    outcome = asyncio.run(service.process(payload, _directive(ttl_seconds=3600)))
    ref = outcome.ref
    assert ref is not None
    assert ref.object_key == f"pjsk/api/pjsk/honor/{digest}.png"
    assert ref.cdn_path == ref.object_key
    assert not ref.object_key.startswith("custom-group")
    assert "some/root" not in ref.object_key
    assert ref.expires_at == "2026-09-13T01:00:00Z"
    assert ref.width == 10
    assert ref.height is None
    assert ref.to_json()["kind"] == ref_mod.ARTIFACT_KIND
