"""Failed renders carry a structured ``code`` next to the unchanged ``detail`` text.

Callers (Haruki-Cloud's upstreamerr) classify by the code; the detail text stays as it was so callers that
still match it keep working.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, HTTPException
import httpx
import pytest

from src.core.heavy_render_pool import HeavyRenderTaskExecutionError
from src.core.main import app as service_app
from src.core.pjsk import deck
from src.core.render_errors import RenderHTTPException, render_http_exception, render_http_exception_handler
from src.sekai.base import render_errors
from src.sekai.base.plot import HSplit, Spacer, _check_canvas_size
from src.sekai.base.render_errors import (
    RenderContentTooLargeError,
    RenderDataInsufficientError,
    render_error_code,
)


class UnidentifiedImageError(OSError):
    """Stands in for PIL's class, matched by name (production has no Pillow)."""


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (FileNotFoundError("图片文件不存在: a.png"), render_errors.ASSET_MISSING),
        (IndexError("list index out of range"), render_errors.DATA_INSUFFICIENT),
        (RenderDataInsufficientError("too few points"), render_errors.DATA_INSUFFICIENT),
        (RenderContentTooLargeError("Canvas size is too large (1x1)"), render_errors.CONTENT_TOO_LARGE),
        (UnidentifiedImageError("cannot identify image file"), render_errors.ASSET_BROKEN),
        (
            HeavyRenderTaskExecutionError("FileNotFoundError: x", render_errors.ASSET_MISSING),
            render_errors.ASSET_MISSING,
        ),
        (HeavyRenderTaskExecutionError("boom"), None),
        (RuntimeError("boom"), None),
        (ValueError("Content size is too large"), None),  # only the typed error carries the code
    ],
)
def test_render_error_code(exc: BaseException, code: str | None) -> None:
    assert render_error_code(exc) == code


def test_unknown_explicit_codes_are_ignored() -> None:
    exc = RuntimeError("x")
    exc.render_error_code = "made_up"  # type: ignore[attr-defined]
    assert render_error_code(exc) is None


def test_render_http_exception_keeps_detail_and_adds_code() -> None:
    known = render_http_exception(FileNotFoundError("图片文件不存在: a.png"))
    assert isinstance(known, RenderHTTPException)
    assert (known.status_code, known.detail, known.code) == (500, "图片文件不存在: a.png", "asset_missing")
    unknown = render_http_exception(RuntimeError("boom"))
    assert type(unknown) is HTTPException
    assert (unknown.status_code, unknown.detail) == (500, "boom")


def test_error_body_carries_code_and_detail() -> None:
    app = FastAPI()
    app.add_exception_handler(RenderHTTPException, render_http_exception_handler)

    @app.get("/missing")
    async def missing():
        raise render_http_exception(FileNotFoundError("图片文件不存在: a.png"))

    @app.get("/unknown")
    async def unknown():
        raise render_http_exception(RuntimeError("boom"))

    async def _get(url: str) -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(url)

    response = asyncio.run(_get("/missing"))
    assert response.status_code == 500
    assert response.json() == {"detail": "图片文件不存在: a.png", "code": "asset_missing"}
    response = asyncio.run(_get("/unknown"))
    assert response.status_code == 500
    assert response.json() == {"detail": "boom"}


def test_service_app_registers_the_handler() -> None:
    assert service_app.exception_handlers.get(RenderHTTPException) is render_http_exception_handler


def test_canvas_size_guard_is_typed() -> None:
    _check_canvas_size((4096, 4096))
    with pytest.raises(RenderContentTooLargeError, match="Canvas size is too large"):
        _check_canvas_size((4097, 4096))


def test_content_overflow_is_typed() -> None:
    row = HSplit().set_padding(0).set_size((5, 5))
    row.add_item(Spacer(w=20, h=20))
    with pytest.raises(RenderContentTooLargeError, match="Content size is too large"):
        row._get_self_size()


def test_heavy_route_forwards_the_worker_code(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Pool:
        async def render(self, kind: str, request: dict[str, object]) -> object:
            raise HeavyRenderTaskExecutionError("FileNotFoundError: a.png", render_errors.ASSET_MISSING)

    class _Request:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            return {}

    monkeypatch.setattr(deck, "get_heavy_render_worker_pool", lambda: _Pool())
    with pytest.raises(RenderHTTPException) as raised:
        asyncio.run(deck.deck_recommend(_Request()))
    assert (raised.value.status_code, raised.value.code) == (500, "asset_missing")


def test_render_failures_are_logged_and_counted(caplog) -> None:
    """A failure raised before render_canvas_payload never reaches the per-endpoint outcome counters, so
    `render_http_exception` logs it at WARN and counts it in /render-stats by path and code."""
    from src.core.debug import pop_request_context, push_request_context, set_request_stage
    from src.sekai.skia_renderer.render_stats import get_render_stats, reset_render_stats

    reset_render_stats()
    tokens = push_request_context("req-1", "/api/pjsk/sk/player-trace", "POST")
    try:
        set_request_stage("sk:player_trace")
        with caplog.at_level(logging.WARNING, logger="src.core.render_errors"):
            render_http_exception(RenderDataInsufficientError("player trace requires at least one rank entry"))
            render_http_exception(RuntimeError("boom"))
        failures = get_render_stats()["request_failures"]
    finally:
        pop_request_context(tokens)
        reset_render_stats()

    assert failures == {
        "total": 2,
        "by_code": {"data_insufficient": 1, "unknown": 1},
        "by_path": {"/api/pjsk/sk/player-trace": {"data_insufficient": 1, "unknown": 1}},
    }
    records = [record for record in caplog.records if "render.failed" in record.getMessage()]
    assert [record.levelno for record in records] == [logging.WARNING, logging.WARNING]
    known, unknown = records
    assert "path=/api/pjsk/sk/player-trace stage=sk:player_trace status=500 code=data_insufficient" in (
        known.getMessage()
    )
    assert "error_type=RenderDataInsufficientError" in known.getMessage()
    assert known.exc_info is None  # a known reason needs no traceback
    assert "code=- error_type=RuntimeError detail=boom" in unknown.getMessage()
    assert unknown.exc_info is not None


def test_logged_failure_detail_is_truncated(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="src.core.render_errors"):
        exc = render_http_exception(RuntimeError("x" * 1000))
    assert exc.detail == "x" * 1000  # the response body is unchanged
    (record,) = [record for record in caplog.records if "render.failed" in record.getMessage()]
    assert record.getMessage().endswith("x" * 300 + "...")


def test_render_stats_endpoint_exposes_request_failures() -> None:
    from src.core.health import render_stats
    from src.sekai.skia_renderer.render_stats import reset_render_stats

    reset_render_stats()
    try:
        body = asyncio.run(render_stats())
    finally:
        reset_render_stats()
    assert body["renders"]["request_failures"] == {"total": 0, "by_code": {}, "by_path": {}}
