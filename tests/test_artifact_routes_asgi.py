"""End-to-end over ASGI: `/api/pjsk/honor` in all four exit shapes, `/render-stats` deltas, and OpenAPI."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from scripts.skia_service_no_pillow import route_for_case
from src.artifact import stats as artifact_stats_mod
from src.artifact.runtime import ArtifactRuntime, set_artifact_runtime
from src.core.http_responses import ARTIFACT_REF_SCHEMA, ARTIFACT_RESPONSES
from src.core.image_payload import EncodedImagePayload
from src.core.pjsk import honor
from src.settings import settings
from tests.storage_fakes import FakeObjectStore, FakeRenderIndex, build_test_runtime

PNG_LIKE = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
KEY = "fedcba9876543210fedc"
ROUTE = "/api/pjsk/honor"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings.storage, "node_name", "cn-asgi")
    artifact_stats_mod.reset_artifact_node_name()
    artifact_stats_mod.reset_artifact_stats()

    async def native_renderer(_request: Any) -> EncodedImagePayload:
        return EncodedImagePayload(
            image_bytes=PNG_LIKE,
            media_type="image/png",
            filename="honor.png",
            image_width=8,
            image_height=4,
            image_mode="RGBA",
            encode_elapsed=0.001,
            backend="skia",
        )

    monkeypatch.setattr(honor, "try_render_full_honor_payload", native_renderer)
    yield
    set_artifact_runtime(None)
    artifact_stats_mod.reset_artifact_node_name()
    artifact_stats_mod.reset_artifact_stats()


def _directive_headers(**extra: str) -> dict[str, str]:
    headers = {
        "X-Haruki-Artifact": "1",
        "X-Haruki-Cache-Key": KEY,
        "X-Haruki-Cache-TTL": "0",
        "X-Haruki-Cache-Key-Version": "3",
        "X-Haruki-Api-Path": ROUTE,
        "X-Haruki-User-Id": "u123",
    }
    headers.update(extra)
    return headers


async def _session(requests: list[tuple[str, str, dict[str, str] | None]]) -> list[httpx.Response]:
    from src.core.main import app

    transport = httpx.ASGITransport(app=app)
    responses = []
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for method, path, headers in requests:
            responses.append(await client.request(method, path, headers=headers, json={} if method == "POST" else None))
    return responses


def _run(*requests: tuple[str, str, dict[str, str] | None]) -> list[httpx.Response]:
    return asyncio.run(_session(list(requests)))


def test_no_directive_returns_bytes_with_node_header() -> None:
    (response, stats) = _run(("POST", ROUTE, None), ("GET", "/render-stats", None))
    assert response.status_code == 200
    assert response.content == PNG_LIKE
    assert response.headers["content-type"] == "image/png"
    assert response.headers["X-Haruki-Node"] == "cn-asgi"
    assert "X-Haruki-Artifact" not in response.headers
    artifacts = stats.json()["artifacts"]
    assert artifacts["bytes_no_directive"] == 1
    assert artifacts["requests_with_directive"] == 0


def test_store_zero_returns_bytes_and_stores_nothing() -> None:
    store = FakeObjectStore(bucket="image-cache")
    index = FakeRenderIndex()
    set_artifact_runtime(build_test_runtime(store=store, index=index))

    (response, stats) = _run(
        ("POST", ROUTE, _directive_headers(**{"X-Haruki-Cache-Store": "0"})), ("GET", "/render-stats", None)
    )

    assert response.status_code == 200
    assert response.content == PNG_LIKE
    assert response.headers["X-Haruki-Cache-Store"] == "0"
    assert response.headers["X-Haruki-Node"] == "cn-asgi"
    assert store.writes == []
    assert index.calls == []
    assert stats.json()["artifacts"]["store_skipped"] == 1


def test_disabled_storage_degrades_to_bytes() -> None:
    set_artifact_runtime(ArtifactRuntime(reason="disabled"))

    (response, stats) = _run(("POST", ROUTE, _directive_headers()), ("GET", "/render-stats", None))

    assert response.status_code == 200
    assert response.content == PNG_LIKE
    assert response.headers["X-Haruki-Artifact-Degraded"] == "1"
    assert response.headers["X-Haruki-Node"] == "cn-asgi"
    assert stats.json()["artifacts"]["degraded"]["disabled"] == 1


def test_artifact_mode_returns_the_ref_and_writes_both_rows() -> None:
    store = FakeObjectStore(bucket="image-cache")
    index = FakeRenderIndex()
    set_artifact_runtime(build_test_runtime(store=store, index=index, node_name="cn-asgi"))

    (first, second, stats) = _run(
        ("POST", ROUTE, _directive_headers()),
        ("POST", ROUTE, _directive_headers()),
        ("GET", "/render-stats", None),
    )

    assert first.status_code == 200
    assert first.headers["X-Haruki-Artifact"] == "1"
    assert first.headers["X-Haruki-Node"] == "cn-asgi"
    assert first.headers["content-length"] == str(len(first.content))
    ref = first.json()
    assert set(ref) == set(ARTIFACT_REF_SCHEMA["required"])
    assert ref["kind"] == "artifact_ref"
    assert ref["object_key"] == ref["cdn_path"]
    assert ref["object_key"].startswith("pjsk/api/pjsk/honor/")
    assert ref["expires_at"] is None
    assert ref["ttl_seconds"] == 0
    assert ref["reused"] is False
    assert ref["index_written"] is True
    assert store.objects[ref["object_key"]] == PNG_LIKE
    assert index.requests[KEY].user_id == "u123"

    reused = second.json()
    assert reused["reused"] is True
    assert reused["cdn_path"] == ref["cdn_path"]
    assert len(store.writes) == 1

    artifacts = stats.json()["artifacts"]
    assert artifacts["requests_with_directive"] == 2
    assert artifacts["published"] == 1
    assert artifacts["reused"] == 1
    assert artifacts["node_name"] == "cn-asgi"


def test_invalid_directive_is_a_400_with_the_node_header() -> None:
    (response,) = _run(("POST", ROUTE, _directive_headers(**{"X-Haruki-Cache-Key": "nothex"})))
    assert response.status_code == 400
    assert response.headers["X-Haruki-Directive-Error"] == "X-Haruki-Cache-Key"
    assert response.headers["X-Haruki-Node"] == "cn-asgi"


def test_openapi_has_one_post_per_drawing_path_with_the_artifact_response() -> None:
    from scripts.skia_parity_sweep import CASES
    from src.core.main import app

    schema = app.openapi()
    drawing = {path: ops for path, ops in schema["paths"].items() if path.startswith("/api/pjsk")}
    assert drawing
    for path, operations in drawing.items():
        assert list(operations) == ["post"], path
        content = operations["post"]["responses"]["200"]["content"]
        assert set(content) == {"application/json", "image/png", "image/jpeg"}, path
        assert content["application/json"]["schema"]["title"] == "ArtifactRef", path
    resolved = [route_for_case(case, schema) for case in CASES]
    assert set(resolved) == set(drawing)
    assert set(ARTIFACT_RESPONSES) == {200}


def test_the_ref_schema_names_exactly_the_ref_fields() -> None:
    from src.artifact.ref import REF_FIELDS

    assert tuple(ARTIFACT_REF_SCHEMA["required"]) == REF_FIELDS
    assert set(ARTIFACT_REF_SCHEMA["properties"]) == set(REF_FIELDS)
