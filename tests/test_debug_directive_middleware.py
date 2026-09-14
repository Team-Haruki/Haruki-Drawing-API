"""Directive wiring in the debug middleware (plan §8.2, T11)."""

from __future__ import annotations

import asyncio
import contextvars
import logging
from unittest.mock import MagicMock

from fastapi import FastAPI
import httpx
import pytest

from src.artifact import stats as artifact_stats_mod
from src.artifact.directive import RenderCacheDirective
from src.core import debug
from src.settings import settings

KEY = "0123456789abcdef0123"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings.storage, "node_name", "cn-test")
    artifact_stats_mod.reset_artifact_node_name()
    artifact_stats_mod.reset_artifact_stats()
    debug.reset_http_request_stats()
    yield
    artifact_stats_mod.reset_artifact_node_name()
    artifact_stats_mod.reset_artifact_stats()
    debug.reset_http_request_stats()


def _valid_headers(**extra: str) -> dict[str, str]:
    headers = {
        "X-Haruki-Artifact": "1",
        "X-Haruki-Cache-Key": KEY,
        "X-Haruki-Cache-TTL": "600",
        "X-Haruki-Cache-Key-Version": "3",
        "X-Haruki-Api-Path": "/api/pjsk/honor",
    }
    headers.update(extra)
    return headers


def _app(route_mock: MagicMock, seen: list[RenderCacheDirective | None]) -> FastAPI:
    app = FastAPI()
    debug.install_debug_middleware(app)

    @app.post("/api/pjsk/honor")
    async def honor():
        route_mock()
        seen.append(debug.current_render_directive())
        return {"ok": True}

    return app


async def _send(app: FastAPI, method: str, path: str, headers: dict[str, str] | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, headers=headers, json={"x": 1} if method == "POST" else None)


@pytest.mark.parametrize(
    ("headers", "header", "code"),
    [
        ({"X-Haruki-Artifact": "yes"}, "X-Haruki-Artifact", "malformed"),
        (_valid_headers(**{"X-Haruki-Cache-Key": "nothex"}), "X-Haruki-Cache-Key", "malformed"),
        (_valid_headers(**{"X-Haruki-Cache-TTL": str(30 * 86400 + 1)}), "X-Haruki-Cache-TTL", "too_large"),
        (_valid_headers(**{"X-Haruki-Api-Path": "api/../x"}), "X-Haruki-Api-Path", "dot_segment"),
    ],
)
def test_invalid_directive_is_rejected_before_route(
    headers: dict[str, str], header: str, code: str, caplog: pytest.LogCaptureFixture
) -> None:
    route_mock = MagicMock()
    app = _app(route_mock, [])
    caplog.set_level(logging.WARNING, logger="src.core.debug")

    response = asyncio.run(_send(app, "POST", "/api/pjsk/honor", headers))

    assert response.status_code == 400
    assert response.json() == {"detail": f"invalid {header}: {code}", "header": header, "code": code}
    assert response.headers["X-Haruki-Directive-Error"] == header
    assert response.headers["X-Haruki-Node"] == "cn-test"
    route_mock.assert_not_called()
    stats = debug.get_http_request_stats()
    assert stats["status_codes"] == {"400": 1}
    assert stats["total"] == 1
    assert artifact_stats_mod.get_artifact_stats()["directive_rejected"] == {header: 1}
    assert artifact_stats_mod.get_artifact_stats()["requests_with_directive"] == 0
    assert any("request.reject_directive" in r.getMessage() and code in r.getMessage() for r in caplog.records)
    assert debug._inflight_requests == 0


def test_valid_directive_reaches_route_and_is_reset(caplog: pytest.LogCaptureFixture) -> None:
    route_mock = MagicMock()
    seen: list[RenderCacheDirective | None] = []
    app = _app(route_mock, seen)
    caplog.set_level(logging.INFO, logger="src.core.debug")

    response = asyncio.run(_send(app, "POST", "/api/pjsk/honor", _valid_headers(**{"X-Haruki-Cache-Store": "0"})))

    assert response.status_code == 200
    route_mock.assert_called_once()
    assert seen == [
        RenderCacheDirective(
            cache_key=KEY,
            key_version=3,
            ttl_seconds=600,
            store=False,
            group="pjsk",
            api_path="api/pjsk/honor",
            user_id="public",
        )
    ]
    assert debug.current_render_directive() is None
    assert artifact_stats_mod.get_artifact_stats()["requests_with_directive"] == 1
    assert debug.get_http_request_stats()["status_codes"] == {"200": 1}
    starts = [r.getMessage() for r in caplog.records if r.getMessage().startswith("request.start")]
    assert len(starts) == 1
    assert " artifact=1 " in starts[0]


def test_no_directive_route_sees_none(caplog: pytest.LogCaptureFixture) -> None:
    route_mock = MagicMock()
    seen: list[RenderCacheDirective | None] = []
    app = _app(route_mock, seen)
    caplog.set_level(logging.INFO, logger="src.core.debug")

    response = asyncio.run(_send(app, "POST", "/api/pjsk/honor", {"X-Haruki-Artifact": "0"}))

    assert response.status_code == 200
    assert seen == [None]
    stats = artifact_stats_mod.get_artifact_stats()
    assert stats["requests_with_directive"] == 0
    assert stats["directive_rejected"] == {}
    starts = [r.getMessage() for r in caplog.records if r.getMessage().startswith("request.start")]
    assert " artifact=0 " in starts[0]


def test_pop_request_context_resets_directive() -> None:
    def run() -> None:
        tokens = debug.push_request_context("rid", "/x", "POST")
        directive = RenderCacheDirective(KEY, 3, 0, True, "pjsk", "api/x", "public")
        tokens.render_directive = debug._render_directive_var.set(directive)
        assert debug.current_render_directive() is directive
        debug.pop_request_context(tokens)
        assert tokens.render_directive is None
        assert debug.current_render_directive() is None

    contextvars.copy_context().run(run)


def test_heavy_worker_context_carries_no_directive() -> None:
    """Heavy workers call push_request_context themselves; that never binds a directive."""

    def run() -> None:
        tokens = debug.push_request_context("rid", "/api/pjsk/heavy", "POST")
        try:
            assert tokens.render_directive is None
            assert debug.current_render_directive() is None
        finally:
            debug.pop_request_context(tokens)

    contextvars.copy_context().run(run)


def test_render_stats_reachable_under_overload(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.health import router

    app = FastAPI()
    debug.install_debug_middleware(app)
    app.include_router(router)

    @app.get("/busy")
    async def busy():
        return {"ok": True}

    monkeypatch.setattr(debug, "OVERLOAD_MAX_INFLIGHT_REQUESTS", 1)
    monkeypatch.setattr(debug, "_inflight_requests", 5)
    assert debug.should_reject_for_overload("/render-stats", 99) is None

    async def exercise() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/busy"), await client.get("/render-stats")

    rejected, stats = asyncio.run(exercise())
    assert rejected.status_code == 503
    assert stats.status_code == 200
    assert "artifacts" in stats.json()
