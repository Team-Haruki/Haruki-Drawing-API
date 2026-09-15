"""`scripts/concurrent_fetch_images.py`: `--expect image` is unchanged, `--expect artifact` counts refs.

The free-threaded smoke workflow asserts `summary["ok_images"] == summary["requests"]` on the three bytes-mode
runs and on the memory-provider artifact run, so both counting rules are pinned here against a local aiohttp
server (no Drawing app, no native extension).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
from pathlib import Path

from aiohttp import web
import pytest

import scripts.concurrent_fetch_images as fetch

PNG = b"\x89PNG\r\n\x1a\n" + b"\n" * 64 + b"pixels"
CDN_PATH = "pjsk/api/pjsk/honor/" + "ab" * 32 + ".png"


def _ref(**overrides: object) -> dict[str, object]:
    ref: dict[str, object] = {
        "kind": "artifact_ref",
        "hash": "ab" * 32,
        "cdn_path": CDN_PATH,
        "object_key": CDN_PATH,
        "size_bytes": len(PNG),
        "reused": False,
    }
    ref.update(overrides)
    return ref


async def _serve(app: web.Application, body: Callable[[str], Awaitable[int]]) -> int:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    try:
        return await body(f"http://127.0.0.1:{port}")
    finally:
        await runner.cleanup()


def _app(seen_headers: list[dict[str, str]], *, cdn_bytes: bytes = PNG, cdn_status: int = 200) -> web.Application:
    async def image(request: web.Request) -> web.Response:
        seen_headers.append(dict(request.headers))
        return web.Response(body=PNG, content_type="image/png")

    async def artifact(request: web.Request) -> web.Response:
        seen_headers.append(dict(request.headers))
        if request.headers.get("X-Haruki-Artifact") != "1":
            return web.Response(body=PNG, content_type="image/png")
        return web.json_response(_ref(reused=request.headers.get("X-Test-Reused") == "1"))

    async def wrong_kind(request: web.Request) -> web.Response:
        return web.json_response({"kind": "something_else", "cdn_path": CDN_PATH})

    async def cdn(request: web.Request) -> web.Response:
        return web.Response(body=cdn_bytes, status=cdn_status, content_type="image/png")

    app = web.Application()
    app.router.add_post("/api/pjsk/honor/", artifact)
    app.router.add_post("/image", image)
    app.router.add_post("/wrong", wrong_kind)
    app.router.add_get("/cdn/{tail:.*}", cdn)
    return app


def _run(argv: list[str], app: web.Application, tmp_path: Path) -> tuple[int, dict[str, object]]:
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"x": 1}), encoding="utf-8")
    out = tmp_path / "out"

    async def body(base: str) -> int:
        full = [
            "--base-url",
            base,
            "--payload-file",
            str(payload),
            "--requests",
            "4",
            "--concurrency",
            "2",
            "--output-dir",
            str(out),
            *[arg.replace("{base}", base) for arg in argv],
        ]
        return await fetch.run(full)

    code = asyncio.run(_serve(app, body))
    return code, json.loads((out / "summary.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def test_expect_defaults_to_image_and_sends_no_directive(tmp_path: Path) -> None:
    seen: list[dict[str, str]] = []
    code, summary = _run(["--endpoint", "/image"], _app(seen), tmp_path)

    assert code == 0
    assert summary["expect"] == "image"
    assert summary["ok_images"] == summary["requests"] == 4
    assert summary["artifact_reused"] == 0
    assert not any(name.lower().startswith("x-haruki-") for headers in seen for name in headers)
    assert len(list((tmp_path / "out" / "images").iterdir())) == 4


def test_expect_image_does_not_count_an_artifact_ref(tmp_path: Path) -> None:
    code, summary = _run(
        ["--endpoint", "/api/pjsk/honor/", "--header", "X-Haruki-Artifact=1", "--save-errors"], _app([]), tmp_path
    )

    assert code == 1
    assert summary["ok_images"] == 0
    assert summary["failed"] == 4


def test_expect_artifact_sends_full_directive_and_counts_refs(tmp_path: Path) -> None:
    seen: list[dict[str, str]] = []
    code, summary = _run(
        ["--endpoint", "/api/pjsk/honor/", "--expect", "artifact", "--header", "X-Test-Reused=1"],
        _app(seen),
        tmp_path,
    )

    assert code == 0
    assert summary["expect"] == "artifact"
    assert summary["ok_images"] == summary["requests"] == 4
    assert summary["artifact_reused"] == 4
    headers = seen[0]
    assert headers["X-Haruki-Artifact"] == "1"
    assert headers["X-Haruki-Api-Path"] == "api/pjsk/honor"
    assert headers["X-Haruki-Cache-Store"] == "1"
    assert len(headers["X-Haruki-Cache-Key"]) == 64
    assert len(list((tmp_path / "out" / "refs").iterdir())) == 4


def test_expect_artifact_rejects_image_bytes_and_wrong_kind(tmp_path: Path) -> None:
    code, summary = _run(
        ["--endpoint", "/api/pjsk/honor/", "--expect", "artifact", "--header", "X-Haruki-Artifact=0", "--save-errors"],
        _app([]),
        tmp_path,
    )
    assert code == 1
    assert summary["ok_images"] == 0
    assert len(list((tmp_path / "out" / "errors").iterdir())) == 4

    code, summary = _run(["--endpoint", "/wrong", "--expect", "artifact"], _app([]), tmp_path)
    assert code == 1
    assert summary["ok_images"] == 0


def test_caller_header_overrides_directive_default() -> None:
    headers = fetch.build_artifact_headers("/api/pjsk/honor/", {"x-haruki-cache-store": "0"})

    assert headers["x-haruki-cache-store"] == "0"
    assert "X-Haruki-Cache-Store" not in headers
    assert headers["X-Haruki-Artifact"] == "1"


@pytest.mark.parametrize(
    "body",
    [b"not json", b"\xff\xfe", b"[]", json.dumps({"kind": "artifact_ref"}).encode(), json.dumps({"kind": 1}).encode()],
)
def test_parse_artifact_ref_rejects_malformed_bodies(body: bytes) -> None:
    assert fetch.parse_artifact_ref(body) is None


def test_fetch_cdn_verifies_object_size(tmp_path: Path) -> None:
    code, summary = _run(
        ["--endpoint", "/api/pjsk/honor/", "--expect", "artifact", "--fetch-cdn", "{base}/cdn"], _app([]), tmp_path
    )
    assert code == 0
    assert summary["ok_images"] == 4
    assert len(list((tmp_path / "out" / "images").glob("*_cdn.png"))) == 4


@pytest.mark.parametrize(("cdn_bytes", "cdn_status"), [(b"short", 200), (PNG, 404)])
def test_fetch_cdn_failure_marks_request_failed(tmp_path: Path, cdn_bytes: bytes, cdn_status: int) -> None:
    code, summary = _run(
        ["--endpoint", "/api/pjsk/honor/", "--expect", "artifact", "--fetch-cdn", "{base}/cdn"],
        _app([], cdn_bytes=cdn_bytes, cdn_status=cdn_status),
        tmp_path,
    )
    assert code == 1
    assert summary["ok_images"] == 0


def test_fetch_cdn_unreachable_marks_request_failed(tmp_path: Path) -> None:
    code, summary = _run(
        ["--endpoint", "/api/pjsk/honor/", "--expect", "artifact", "--fetch-cdn", "http://127.0.0.1:9"],
        _app([]),
        tmp_path,
    )
    assert code == 1
    assert summary["ok_images"] == 0


def test_fetch_cdn_requires_artifact_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--fetch-cdn requires --expect artifact"):
        _run(["--endpoint", "/image", "--fetch-cdn", "{base}/cdn"], _app([]), tmp_path)
