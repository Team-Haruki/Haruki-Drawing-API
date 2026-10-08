"""Store-ref mode (`X-Haruki-Artifact-Mode: store-ref`, docs/artifact-storage.md §12): upload-only, no index."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import re
from typing import Any

import httpx
import pytest

from src.artifact import stats as artifact_stats_mod
from src.artifact.directive import RenderCacheDirective, parse_render_cache_directive
from src.artifact.ref import REF_FIELDS, build_store_ref_key, new_store_ref_generation
from src.artifact.runtime import ArtifactRuntime, build_artifact_runtime, set_artifact_runtime
from src.core import debug
from src.core.image_payload import EncodedImagePayload
from src.core.pjsk import honor
from src.core.utils import encoded_image_payload_to_response
from src.settings import Settings, StorageSettings, settings
from src.storage.protocols import StorageUnavailable
from tests.storage_fakes import FakeObjectStore, FakeRenderIndex, build_test_runtime

PNG_LIKE = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8
JPEG_LIKE = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4
KEY = "0123456789abcdef0123"
DIGEST = hashlib.sha256(PNG_LIKE).hexdigest()
STORE_REF_KEY = re.compile(r"pjsk/(?P<hash>[0-9a-f]{64})-(?P<gen>[A-Z2-7]{26})\.(?P<ext>png|jpg)")
TTL_MAX = 30 * 86400


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings.storage, "node_name", "cn-render")
    artifact_stats_mod.reset_artifact_node_name()
    artifact_stats_mod.reset_artifact_stats()
    yield
    set_artifact_runtime(None)
    artifact_stats_mod.reset_artifact_node_name()
    artifact_stats_mod.reset_artifact_stats()


def _payload(**overrides: Any) -> EncodedImagePayload:
    values: dict[str, Any] = {
        "image_bytes": PNG_LIKE,
        "media_type": "image/png",
        "filename": "x.png",
        "image_width": 4,
        "image_height": 4,
        "image_mode": "RGBA",
        "encode_elapsed": 0.0,
    }
    values.update(overrides)
    return EncodedImagePayload(**values)


def _directive(*, store: bool = False, store_ref: bool = True, **overrides: Any) -> RenderCacheDirective:
    values: dict[str, Any] = {
        "cache_key": KEY,
        "key_version": 6,
        "ttl_seconds": 600,
        "store": store,
        "group": "pjsk",
        "api_path": "api/pjsk/sk/line",
        "user_id": "u1",
        "store_ref": store_ref,
    }
    values.update(overrides)
    return RenderCacheDirective(**values)


def _drive(response) -> list[dict]:
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.disconnect"}

    scope = {"type": "http", "method": "POST", "path": "/x", "headers": []}
    asyncio.run(response(scope, receive, send))
    return sent


def _exit(payload: EncodedImagePayload, directive: RenderCacheDirective | None):
    def run():
        tokens = debug.push_request_context("rid-store-ref", "/api/pjsk/sk/line", "POST")
        token = debug._render_directive_var.set(directive)
        try:
            return asyncio.run(encoded_image_payload_to_response(payload))
        finally:
            debug._render_directive_var.reset(token)
            debug.pop_request_context(tokens)

    return _drive(contextvars.copy_context().run(run))


def _headers(sent: list[dict]) -> dict[str, str]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    return {k.decode().lower(): v.decode() for k, v in start["headers"]}


def _bodies(sent: list[dict]) -> list[dict]:
    return [m for m in sent if m["type"] == "http.response.body"]


def _response_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("image.response")]


# ---------------------------------------------------------------------- directive


def _wire(**extra: str) -> dict[str, str]:
    headers = {
        "x-haruki-artifact": "1",
        "x-haruki-cache-key": KEY,
        "x-haruki-cache-ttl": "600",
        "x-haruki-cache-key-version": "6",
        "x-haruki-api-path": "/api/pjsk/sk/line",
    }
    headers.update(extra)
    return headers


@pytest.mark.parametrize(("value", "expected"), [("store-ref", True), (" Store-Ref ", True), (None, False)])
def test_mode_header_selects_store_ref(value: str | None, expected: bool) -> None:
    extra = {} if value is None else {"x-haruki-artifact-mode": value}
    directive = parse_render_cache_directive(_wire(**extra), ttl_max=TTL_MAX)
    assert directive is not None
    assert directive.store_ref is expected


@pytest.mark.parametrize("value", ["", "index", "store_ref", "1"])
def test_unknown_mode_is_ignored_not_rejected(value: str) -> None:
    directive = parse_render_cache_directive(_wire(**{"x-haruki-artifact-mode": value}), ttl_max=TTL_MAX)
    assert directive is not None
    assert directive.store_ref is False


def test_mode_header_without_artifact_is_never_read() -> None:
    headers = {"x-haruki-artifact-mode": "store-ref"}
    assert parse_render_cache_directive(headers, ttl_max=TTL_MAX) is None


# ---------------------------------------------------------------------- keys


def test_store_ref_key_is_clouds_image_cache_layout() -> None:
    generation = new_store_ref_generation()
    assert re.fullmatch(r"[A-Z2-7]{26}", generation)
    assert new_store_ref_generation() != generation
    key = build_store_ref_key(DIGEST, "image/jpeg", generation=generation)
    assert key == f"pjsk/{DIGEST}-{generation}.jpg"
    assert build_store_ref_key(DIGEST, "image/png", generation="G").endswith("-G.png")


# ---------------------------------------------------------------------- exit


def test_store_ref_uploads_without_index_and_returns_one_json_body(caplog: pytest.LogCaptureFixture) -> None:
    store = FakeObjectStore(bucket="image-cache")
    index = FakeRenderIndex()
    set_artifact_runtime(build_test_runtime(store=store, index=index, node_name="cn-render"))
    caplog.set_level(logging.INFO, logger="src.core.utils")

    sent = _exit(_payload(), _directive())

    bodies = _bodies(sent)
    assert len(bodies) == 1
    headers = _headers(sent)
    assert headers["x-haruki-artifact"] == "1"
    assert headers["x-haruki-artifact-mode"] == "store-ref"
    assert headers["x-haruki-cache-store"] == "0"
    assert headers["x-haruki-node"] == "cn-render"
    assert headers["content-type"] == "application/json"
    assert headers["content-length"] == str(len(bodies[0]["body"]))
    assert "x-haruki-artifact-degraded" not in headers

    document = json.loads(bodies[0]["body"])
    assert tuple(document) == REF_FIELDS
    match = STORE_REF_KEY.fullmatch(document["cdn_path"])
    assert match is not None
    assert match["hash"] == DIGEST == document["hash"]
    assert match["ext"] == "png"
    assert document["object_key"] == document["cdn_path"]
    assert document["kind"] == "artifact_ref"
    assert document["storage_backend"] == "garage"
    assert document["bucket"] == "image-cache"
    assert document["size_bytes"] == len(PNG_LIKE)
    assert document["media_type"] == "image/png"
    assert (document["width"], document["height"]) == (4, 4)
    assert document["cache_key"] == KEY
    assert document["reused"] is False
    assert document["index_written"] is False
    assert document["node_name"] == "cn-render"
    assert document["upload_elapsed"] >= 0

    assert store.writes == [(document["object_key"], len(PNG_LIKE), "image/png")]
    assert store.objects[document["object_key"]] == PNG_LIKE
    assert index.calls == []

    lines = _response_lines(caplog)
    assert len(lines) == 1
    assert f" artifact=store_ref hash={DIGEST} writer=cn-render upload=" in lines[0]
    stages = re.search(r" stages=(\S+) ", lines[0])
    assert stages is not None
    assert [part.split(":")[0] for part in stages.group(1).split(",")] == ["hash", "upload", "total"]

    snapshot = artifact_stats_mod.get_artifact_stats()
    assert snapshot["store_ref"]["requests"] == 1
    assert snapshot["store_ref"]["published"] == 1
    assert snapshot["store_ref"]["upload_bytes"] == len(PNG_LIKE)
    assert snapshot["store_ref"]["stages"]["upload"]["count"] == 1
    assert snapshot["store_ref"]["stages"]["total"]["count"] == 1
    # Artifact-mode counters and stages stay untouched: no index, no publish, no store_skipped.
    assert snapshot["published"] == 0
    assert snapshot["store_skipped"] == 0
    assert snapshot["index_lookups"] == 0
    assert snapshot["stages"]["total"]["count"] == 0
    assert snapshot["stages"]["upload"]["count"] == 0
    # The object write itself is counted with every other upload.
    assert snapshot["uploads"] == 1


def test_store_ref_works_without_an_index() -> None:
    store = FakeObjectStore(bucket="image-cache")
    set_artifact_runtime(build_test_runtime(store=store, index=None))

    sent = _exit(_payload(), _directive())

    assert _headers(sent)["x-haruki-artifact-mode"] == "store-ref"
    assert len(store.writes) == 1


def test_jpeg_gets_a_jpg_key() -> None:
    store = FakeObjectStore(bucket="image-cache")
    set_artifact_runtime(build_test_runtime(store=store))

    sent = _exit(_payload(image_bytes=JPEG_LIKE, media_type="image/jpeg", filename="x.jpg"), _directive())

    document = json.loads(_bodies(sent)[0]["body"])
    assert document["cdn_path"].endswith(".jpg")
    assert document["hash"] == hashlib.sha256(JPEG_LIKE).hexdigest()
    assert store.content_types[document["cdn_path"]] == "image/jpeg"


def test_each_store_ref_gets_a_fresh_key() -> None:
    store = FakeObjectStore(bucket="image-cache")
    set_artifact_runtime(build_test_runtime(store=store))

    first = json.loads(_bodies(_exit(_payload(), _directive()))[0]["body"])
    second = json.loads(_bodies(_exit(_payload(), _directive()))[0]["body"])

    assert first["hash"] == second["hash"]
    assert first["cdn_path"] != second["cdn_path"]
    assert len(store.writes) == 2


def test_writer_node_names_the_garage_gateway() -> None:
    store = FakeObjectStore(bucket="image-cache")
    set_artifact_runtime(build_test_runtime(store=store, node_name="cn-render", writer_node="gw-1"))

    sent = _exit(_payload(), _directive())

    assert json.loads(_bodies(sent)[0]["body"])["node_name"] == "gw-1"
    assert _headers(sent)["x-haruki-node"] == "cn-render"


def test_writer_node_comes_from_settings() -> None:
    store = FakeObjectStore(bucket="image-cache")
    storage = StorageSettings(enabled=True, writer_node=" gw-2 ")
    set_artifact_runtime(build_test_runtime(store=store, settings=storage, node_name="cn-render"))

    sent = _exit(_payload(), _directive())

    assert json.loads(_bodies(sent)[0]["body"])["node_name"] == "gw-2"


def test_missing_assets_still_upload_with_cache_store_zero() -> None:
    store = FakeObjectStore(bucket="image-cache")
    set_artifact_runtime(build_test_runtime(store=store))

    sent = _exit(_payload(missing_asset_count=2), _directive())

    headers = _headers(sent)
    assert headers["x-haruki-artifact-mode"] == "store-ref"
    assert headers["x-haruki-cache-store"] == "0"
    assert len(store.writes) == 1
    assert artifact_stats_mod.get_artifact_stats()["store_ref"]["missing_assets"] == 1


def test_stale_renderer_epoch_still_uploads() -> None:
    store = FakeObjectStore(bucket="image-cache")
    set_artifact_runtime(build_test_runtime(store=store))

    sent = _exit(_payload(), _directive(renderer_epoch="f" * 64))

    assert _headers(sent)["x-haruki-artifact-mode"] == "store-ref"
    assert len(store.writes) == 1


@pytest.mark.parametrize(
    ("fail", "reason"),
    [(StorageUnavailable("down"), "upload_failed"), (RuntimeError("boom"), "internal")],
)
def test_upload_failure_degrades_to_bytes(fail: Exception, reason: str, caplog: pytest.LogCaptureFixture) -> None:
    store = FakeObjectStore(bucket="image-cache", fail=fail)
    set_artifact_runtime(build_test_runtime(store=store, index=FakeRenderIndex()))
    caplog.set_level(logging.INFO, logger="src.core.utils")

    sent = _exit(_payload(), _directive())

    headers = _headers(sent)
    assert headers["x-haruki-artifact-degraded"] == "1"
    assert headers["x-haruki-cache-store"] == "0"
    assert headers["content-type"] == "image/png"
    assert "x-haruki-artifact" not in headers
    assert "x-haruki-artifact-mode" not in headers
    assert _bodies(sent)[0]["body"] == PNG_LIKE
    lines = _response_lines(caplog)
    assert f" artifact=store_ref_degraded reason={reason} stages=hash:" in lines[0]
    snapshot = artifact_stats_mod.get_artifact_stats()
    assert snapshot["store_ref"]["degraded"][reason] == 1
    assert snapshot["store_ref"]["published"] == 0
    assert sum(snapshot["degraded"].values()) == 0


def test_upload_timeout_degrades_to_bytes() -> None:
    store = FakeObjectStore(bucket="image-cache", delay=0.5)
    storage = StorageSettings(enabled=True, upload_timeout_seconds=0.01)
    set_artifact_runtime(build_test_runtime(store=store, settings=storage))

    sent = _exit(_payload(), _directive())

    assert _headers(sent)["x-haruki-artifact-degraded"] == "1"
    assert artifact_stats_mod.get_artifact_stats()["store_ref"]["degraded"]["upload_timeout"] == 1


@pytest.mark.parametrize("reason", ["disabled", "runtime_unavailable"])
def test_unavailable_runtime_degrades_to_bytes(reason: str) -> None:
    set_artifact_runtime(ArtifactRuntime(reason=reason))

    sent = _exit(_payload(), _directive())

    headers = _headers(sent)
    assert headers["x-haruki-artifact-degraded"] == "1"
    assert headers["x-haruki-cache-store"] == "0"
    assert _bodies(sent)[0]["body"] == PNG_LIKE
    snapshot = artifact_stats_mod.get_artifact_stats()
    assert snapshot["store_ref"]["requests"] == 1
    assert snapshot["store_ref"]["degraded"][reason] == 1


def test_unsupported_media_degrades_to_bytes() -> None:
    store = FakeObjectStore(bucket="image-cache")
    set_artifact_runtime(build_test_runtime(store=store))

    sent = _exit(_payload(media_type="image/webp", filename="x.webp"), _directive())

    assert _headers(sent)["x-haruki-artifact-degraded"] == "1"
    assert store.writes == []
    assert artifact_stats_mod.get_artifact_stats()["store_ref"]["degraded"]["unsupported_media"] == 1


def test_internal_error_degrades_to_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeObjectStore(bucket="image-cache")
    runtime = build_test_runtime(store=store)
    set_artifact_runtime(runtime)

    async def broken_hash(*_args: Any, **_kwargs: Any) -> str:
        raise ValueError("bug")

    monkeypatch.setattr(runtime.service, "_hash", broken_hash)

    sent = _exit(_payload(), _directive())

    assert _headers(sent)["x-haruki-artifact-degraded"] == "1"
    assert artifact_stats_mod.get_artifact_stats()["store_ref"]["degraded"]["internal"] == 1


def test_store_one_ignores_the_mode_and_runs_artifact_mode() -> None:
    store = FakeObjectStore(bucket="image-cache")
    index = FakeRenderIndex()
    set_artifact_runtime(build_test_runtime(store=store, index=index))

    sent = _exit(_payload(), _directive(store=True, store_ref=True, api_path="api/pjsk/honor"))

    headers = _headers(sent)
    assert "x-haruki-artifact-mode" not in headers
    document = json.loads(_bodies(sent)[0]["body"])
    assert document["index_written"] is True
    assert document["object_key"].startswith("pjsk/api/pjsk/honor/")
    assert [name for name, _ in index.calls] == ["prepare_upload", "record_upload"]


def test_store_zero_without_the_mode_is_unchanged() -> None:
    store = FakeObjectStore(bucket="image-cache")
    set_artifact_runtime(build_test_runtime(store=store))

    sent = _exit(_payload(), _directive(store_ref=False))

    headers = _headers(sent)
    assert headers["x-haruki-cache-store"] == "0"
    assert "x-haruki-artifact-mode" not in headers
    assert _bodies(sent)[0]["body"] == PNG_LIKE
    assert store.writes == []
    assert artifact_stats_mod.get_artifact_stats()["store_skipped"] == 1


def test_disabled_storage_runtime_builds_without_store() -> None:
    runtime = build_artifact_runtime(Settings(storage=StorageSettings(enabled=False)))
    outcome = asyncio.run(runtime.process_store_ref(_payload(), _directive()))
    assert outcome.degraded is True
    assert outcome.reason == "disabled"


# ---------------------------------------------------------------------- over ASGI


def test_store_ref_over_asgi_and_render_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    async def native_renderer(_request: Any) -> EncodedImagePayload:
        return _payload(backend="skia")

    monkeypatch.setattr(honor, "try_render_full_honor_payload", native_renderer)
    store = FakeObjectStore(bucket="image-cache")
    set_artifact_runtime(build_test_runtime(store=store, index=FakeRenderIndex()))
    headers = {
        "X-Haruki-Artifact": "1",
        "X-Haruki-Cache-Key": KEY,
        "X-Haruki-Cache-TTL": "0",
        "X-Haruki-Cache-Key-Version": "6",
        "X-Haruki-Api-Path": "/api/pjsk/honor",
        "X-Haruki-Cache-Store": "0",
        "X-Haruki-Artifact-Mode": "store-ref",
    }

    async def session() -> tuple[httpx.Response, httpx.Response]:
        from src.core.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/api/pjsk/honor", headers=headers, json={})
            stats = await client.get("/render-stats")
        return response, stats

    response, stats = asyncio.run(session())

    assert response.status_code == 200
    assert response.headers["X-Haruki-Artifact-Mode"] == "store-ref"
    assert response.headers["X-Haruki-Cache-Store"] == "0"
    document = response.json()
    assert STORE_REF_KEY.fullmatch(document["cdn_path"])
    assert document["expires_at"] is None  # TTL 0
    artifacts = stats.json()["artifacts"]
    assert artifacts["requests_with_directive"] == 1
    assert artifacts["store_ref"]["published"] == 1
    assert artifacts["store_ref"]["degraded"] == dict.fromkeys(artifacts["store_ref"]["degraded"], 0)
