from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, Request
import httpx
import pytest

from src.core import debug


def _request(path: str = "/debug", *, body: bytes = b"", content_type: str | None = None) -> Request:
    headers = [] if content_type is None else [(b"content-type", content_type.encode())]
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def test_request_context_stage_and_backend_are_restored() -> None:
    outer_stage = debug._request_stage_var.set(debug.RequestStageRef("outside"))
    outer_backend = debug._render_backend_var.set("outside")
    try:
        tokens = debug.push_request_context("request-1", "/debug", "POST")
        try:
            assert debug.current_request_context() == {
                "request_id": "request-1",
                "path": "/debug",
                "method": "POST",
                "stage": "middleware",
            }
            debug.set_request_stage("  render  ")
            debug.set_render_backend("  skia  ")
            assert debug.current_request_stage() == "render"
            assert debug.current_render_backend() == "skia"
        finally:
            debug.pop_request_context(tokens)

        assert debug.current_request_stage() == "outside"
        assert debug.current_render_backend() == "outside"
    finally:
        debug._request_stage_var.reset(outer_stage)
        debug._render_backend_var.reset(outer_backend)


def test_stage_and_backend_defaults_without_request_context() -> None:
    stage_token = debug._request_stage_var.set(None)
    backend_token = debug._render_backend_var.set("temporary")
    try:
        debug.set_request_stage("")
        debug.set_render_backend("")
        assert debug.current_request_stage() == "unknown"
        assert debug.current_render_backend() == debug.DEFAULT_RENDER_BACKEND
    finally:
        debug._request_stage_var.reset(stage_token)
        debug._render_backend_var.reset(backend_token)


def test_watchdog_warns_once_before_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    watchdog = debug.RequestWatchdog("rid", "GET", "/slow", started_at=0.0)
    sleeps = 0

    async def fake_sleep(_seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            watchdog.cancel()

    warnings: list[tuple[Any, ...]] = []
    monkeypatch.setattr(debug.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(debug.time, "perf_counter", lambda: 12.0)
    monkeypatch.setattr(debug, "snapshot_process_metrics", lambda **_: {"inflight": 1})
    monkeypatch.setattr(debug.logger, "warning", lambda *args, **_: warnings.append(args))

    asyncio.run(watchdog.run())

    assert sleeps == 2
    assert len(warnings) == 1
    assert warnings[0][1:4] == ("rid", "GET", "/slow")


def test_process_status_parsing_and_metric_fail_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    status_path = tmp_path / "status"
    status_path.write_text("VmRSS:\t2048 kB\nIgnored line\nThreads: 7\n")
    assert debug._read_process_status(status_path) == {"VmRSS": "2048 kB", "Threads": "7"}
    assert debug._read_process_status(tmp_path / "missing") == {}
    assert debug._read_process_status(tmp_path) == {}
    assert debug._parse_status_kb({"VmRSS": "2048 kB"}, "VmRSS") == 2.0
    assert debug._parse_status_kb({"VmRSS": "bad kB"}, "VmRSS") is None
    assert debug._parse_status_kb({}, "VmRSS") is None
    assert debug._parse_status_int({"Threads": "7"}, "Threads") == 7
    assert debug._parse_status_int({"Threads": "bad"}, "Threads") is None
    assert debug._parse_status_int({}, "Threads") is None

    monkeypatch.setattr(debug.os, "listdir", lambda _path: (_ for _ in ()).throw(OSError("denied")))
    assert debug._open_fd_count() is None
    assert debug._asyncio_task_count() is None


def test_snapshot_metrics_optionally_includes_asyncio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(debug, "_read_process_status", lambda _path: {"VmRSS": "4096 kB", "Threads": "3"})
    monkeypatch.setattr(debug, "_open_fd_count", lambda: 5)
    monkeypatch.setattr(debug, "_asyncio_task_count", lambda: 2)
    monkeypatch.setattr(debug.os, "getpid", lambda: 99)

    assert debug.snapshot_process_metrics() == {
        "pid": 99,
        "rss_mb": 4.0,
        "threads": 3,
        "fds": 5,
        "inflight": debug._inflight_requests,
    }
    assert debug.snapshot_process_metrics(include_asyncio=True)["asyncio_tasks"] == 2


def test_runtime_thresholds_and_overload_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(debug, "READINESS_UNHEALTHY_INFLIGHT_REQUESTS", 3)
    monkeypatch.setattr(debug, "READINESS_UNHEALTHY_RSS_MB", 100)
    monkeypatch.setattr(debug, "READINESS_UNHEALTHY_ASYNCIO_TASKS", 10)
    monkeypatch.setattr(debug, "READINESS_UNHEALTHY_CGROUP_PERCENT", 90)
    monkeypatch.setattr(debug, "read_cgroup_memory", lambda: None)

    ready, reasons, metrics = debug.evaluate_runtime_readiness({"inflight": 3, "rss_mb": 101.5, "asyncio_tasks": 10})
    assert not ready
    assert reasons == ["inflight 3 >= 3", "rss_mb 101.5 >= 100", "asyncio_tasks 10 >= 10"]
    assert metrics["inflight"] == 3
    assert debug.runtime_readiness_thresholds() == {
        "inflight": 3,
        "rss_mb": 100,
        "asyncio_tasks": 10,
        "cgroup_percent": 90,
    }

    monkeypatch.setattr(debug, "snapshot_process_metrics", lambda **_: {"inflight": 0})
    assert debug.evaluate_runtime_readiness()[0]

    monkeypatch.setattr(debug, "OVERLOAD_MAX_INFLIGHT_REQUESTS", 2)
    assert debug.should_reject_for_overload("/health", 10) is None
    assert debug.should_reject_for_overload("/docs/subpage", 10) is None
    assert debug.should_reject_for_overload("/redoc/subpage", 10) is None
    assert debug.should_reject_for_overload("/api", 2) is None
    assert debug.should_reject_for_overload("/api", 3) == "inflight 3 > 2"
    monkeypatch.setattr(debug, "OVERLOAD_MAX_INFLIGHT_REQUESTS", 0)
    assert debug.should_reject_for_overload("/api", 100) is None


def test_request_body_summary_covers_text_json_binary_and_errors() -> None:
    empty = debug.summarize_request_body(b"", None)
    assert empty["bytes"] == 0
    assert "preview" not in empty

    binary = debug.summarize_request_body(b"\x00\x01", "image/png")
    assert "preview" not in binary

    text = debug.summarize_request_body(("x" * 600 + "\n").encode(), "text/plain")
    assert text["preview"].endswith("...(truncated)")
    assert len(text["preview"]) == debug._BODY_PREVIEW_LIMIT + len("...(truncated)")

    valid = debug.summarize_request_body(b'{"items":[1,2]}', "application/json")
    assert valid["shape"]["list_lengths"] == {"items": 2}
    invalid = debug.summarize_request_body(b"{", "application/json")
    assert "json_error" in invalid

    class BrokenBytes(bytes):
        def decode(self, *args, **kwargs):
            raise RuntimeError("decode failed")

    assert debug._decode_body_preview(BrokenBytes(b"text"), "text/plain") == ""


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b"{}", "text/plain"),
        (b"", "application/json"),
        (b"{", "application/json"),
        (b"[]", "application/json"),
        (b"{}", "application/json"),
    ],
)
def test_request_focus_rejects_unusable_payloads(body: bytes, content_type: str) -> None:
    assert debug.extract_debug_request_focus("/debug", body, content_type) is None


def test_request_focus_extracts_only_bounded_fields() -> None:
    body = (
        b'{"profile":{"id":123,"region":"jp"},"region":"en","honors":[1],"pcards":[1,2],'
        b'"maps":[],"deck_data":[1,2,3],"bg_settings":{"alpha":0.5,"blur":4,"ignored":"secret"}}'
    )
    assert debug.extract_debug_request_focus("/debug", body, "application/json") == {
        "profile_id": 123,
        "profile_region": "jp",
        "region": "en",
        "honors": 1,
        "pcards": 2,
        "maps": 0,
        "decks": 3,
        "bg_settings": {"alpha": 0.5, "blur": 4},
        "path": "/debug",
    }
    assert debug._extract_bg_focus({"bg_settings": "invalid"}) == {}


def test_json_shape_handles_depth_limits_and_all_value_categories() -> None:
    assert debug.summarize_json_shape(None) is None
    assert debug.summarize_json_shape(4) == 4
    assert debug.summarize_json_shape("x" * 81) == "x" * 80 + "..."
    assert debug.summarize_json_shape([]) == {"type": "list", "len": 0}
    assert debug.summarize_json_shape([[1]]) == {
        "type": "list",
        "len": 1,
        "sample": {"type": "list", "len": 1, "sample": "int"},
    }
    assert debug.summarize_json_shape({"items": [1], "value": 2}) == {
        "type": "dict",
        "keys": ["items", "value"],
        "list_lengths": {"items": 1},
    }
    assert debug.summarize_json_shape(object()) == "object"


def test_overload_response_records_retry_header(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request()
    trace = debug._DebugRequestTrace("rid", 1.0, 4)
    monkeypatch.setattr(debug, "should_reject_for_overload", lambda *_: "busy")
    monkeypatch.setattr(debug, "OVERLOAD_RETRY_AFTER_SECONDS", 7)
    monkeypatch.setattr(debug, "snapshot_process_metrics", lambda **_: {})
    debug.reset_http_request_stats()

    response = debug._overload_response(request, trace)

    assert response is not None
    assert response.status_code == 503
    assert response.headers["retry-after"] == "7"
    assert debug.get_http_request_stats()["server_errors"]["by_route"] == {"__overload__": 1}
    debug.reset_http_request_stats()


def test_response_helpers_cover_cache_success_failure_and_absent_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.sekai.base import utils

    assert debug._slow_request_cache_stats(0.0) is None
    monkeypatch.setattr(utils, "get_runtime_cache_stats", lambda: {"image_cache": {"size": 1}})
    assert debug._slow_request_cache_stats(debug._SLOW_REQUEST_SECONDS) == {"image_cache": {"size": 1}}
    monkeypatch.setattr(utils, "get_runtime_cache_stats", lambda: (_ for _ in ()).throw(RuntimeError("broken")))
    assert debug._slow_request_cache_stats(debug._SLOW_REQUEST_SECONDS) == {"error": "broken"}
    assert debug._response_header(SimpleNamespace(), "content-type") is None
    assert debug._response_header(SimpleNamespace(headers={"content-type": "image/png"}), "content-type") == "image/png"


def test_middleware_overload_and_uncaught_exception_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    debug.install_debug_middleware(app)

    @app.get("/boom")
    async def boom():
        raise ValueError("boom")

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            monkeypatch.setattr(debug, "should_reject_for_overload", lambda *_: "busy")
            rejected = await client.get("/boom")
            assert rejected.status_code == 503

            monkeypatch.setattr(debug, "should_reject_for_overload", lambda *_: None)
            with pytest.raises(ValueError, match="boom"):
                await client.get("/boom")

    debug.reset_http_request_stats()
    asyncio.run(exercise())
    stats = debug.get_http_request_stats()
    assert stats["status_codes"] == {"500": 1, "503": 1}
    assert stats["server_errors"]["uncaught_exceptions"] == 1
    assert debug._inflight_requests == 0
    debug.reset_http_request_stats()
