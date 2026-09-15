"""Health endpoints expose the Phase-2 stats blocks (plan §10.2)."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
import httpx
import pytest

from src.artifact import stats as artifact_stats_mod
from src.core import debug
from src.core.health import router
from src.settings import settings


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings.storage, "node_name", "cn-health")
    artifact_stats_mod.reset_artifact_node_name()
    artifact_stats_mod.reset_artifact_stats()
    yield
    artifact_stats_mod.reset_artifact_node_name()
    artifact_stats_mod.reset_artifact_stats()


def _get(path: str) -> httpx.Response:
    app = FastAPI()
    app.include_router(router)

    async def fetch() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(fetch())


def test_render_stats_has_artifacts_block_with_zero_counters() -> None:
    response = _get("/render-stats")
    assert response.status_code == 200
    body = response.json()
    assert {"renders", "http_requests", "skia_payload_cache", "artifacts"} <= set(body)
    artifacts = body["artifacts"]
    assert artifacts["enabled"] is False
    assert artifacts["node_name"] == "cn-health"
    assert artifacts["bucket"] == settings.storage.provider.bucket
    assert artifacts["index"] == {"configured": False, "usable": False, "last_error": None}
    for name in artifact_stats_mod.SIMPLE_COUNTERS:
        assert artifacts[name] == 0
    assert artifacts["upload_elapsed_total"] == 0
    assert set(artifacts["index_skipped"]) == {"disabled", "schema_missing", "unavailable"}
    assert set(artifacts["degraded"]) == {
        "disabled",
        "runtime_unavailable",
        "upload_failed",
        "upload_timeout",
        "unsupported_media",
        "internal",
    }
    assert all(v == 0 for v in artifacts["degraded"].values())
    assert artifacts["directive_rejected"] == {}
    assert set(artifacts["stages"]) == {"hash", "index_lookup", "upload", "index_write"}
    assert artifacts["last_error"] is None


def test_render_stats_reflects_counter_deltas() -> None:
    artifact_stats_mod.artifact_stats.directive_rejected("X-Haruki-Cache-Key")
    artifact_stats_mod.artifact_stats.incr("published")
    body = _get("/render-stats").json()["artifacts"]
    assert body["directive_rejected"] == {"X-Haruki-Cache-Key": 1}
    assert body["published"] == 1


def test_cache_stats_has_mirror_and_missing_assets() -> None:
    response = _get("/cache/stats")
    assert response.status_code == 200
    caches = response.json()["caches"]
    assert "asset_mirror" in caches
    assert "missing_assets" in caches


def test_render_stats_is_exempt_from_overload_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(debug, "OVERLOAD_MAX_INFLIGHT_REQUESTS", 1)
    assert "/render-stats" in debug._EXEMPT_RUNTIME_GUARD_PATHS
    assert debug.should_reject_for_overload("/render-stats", 1000) is None
    assert debug.should_reject_for_overload("/api/pjsk/honor", 1000) is not None
