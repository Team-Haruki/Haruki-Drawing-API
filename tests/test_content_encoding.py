"""zstd request bodies: decoding, limits, refusal and the RFC 7694 advertisement."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from compression import zstd
from fastapi import FastAPI, Request
import httpx
import pytest

from src.core.content_encoding import BodyCodingError, ZstdRequestBodyMiddleware, decode_zstd, request_coding


def _echo_app(limit: int) -> FastAPI:
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, Any]:
        body = await request.body()
        return {
            "len": len(body),
            "head": body[:16].decode(errors="replace"),
            "content_encoding": request.headers.get("content-encoding"),
            "content_length": request.headers.get("content-length"),
        }

    app.add_middleware(ZstdRequestBodyMiddleware, max_decoded_body_bytes=limit)
    return app


def _post(app: FastAPI, body: bytes, headers: dict[str, str] | None = None) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/echo", content=body, headers=headers or {})

    return asyncio.run(run())


def test_decodes_zstd_bodies_before_the_route() -> None:
    payload = json.dumps({"cards": list(range(5000))}).encode()
    response = _post(_echo_app(1 << 20), zstd.compress(payload), {"Content-Encoding": "zstd"})
    assert response.status_code == 200
    data = response.json()
    assert data["len"] == len(payload)
    assert data["head"] == payload[:16].decode()
    assert data["content_encoding"] is None
    assert data["content_length"] == str(len(payload))
    assert response.headers["accept-encoding"] == "zstd"


def test_identity_bodies_pass_through_and_are_advertised() -> None:
    response = _post(_echo_app(1 << 20), b'{"a":1}', {"Content-Encoding": "identity"})
    assert response.status_code == 200
    assert response.json()["len"] == 7
    assert response.headers["accept-encoding"] == "zstd"
    response = _post(_echo_app(1 << 20), b'{"a":1}')
    assert response.status_code == 200
    assert response.headers["accept-encoding"] == "zstd"


@pytest.mark.parametrize(
    ("body", "headers", "status"),
    [
        (zstd.compress(bytes(2 << 20)), {"Content-Encoding": "zstd"}, 413),
        (b"x" * 64, {"Content-Encoding": "zstd"}, 413),
        (b"not a zstd frame", {"Content-Encoding": "zstd"}, 400),
        (zstd.compress(b'{"a":1}')[:-3], {"Content-Encoding": "zstd"}, 400),
        (zstd.compress(b"{}") + b"trailing", {"Content-Encoding": "zstd"}, 400),
        (b"{}", {"Content-Encoding": "gzip"}, 415),
        (b"{}", {"Content-Encoding": "zstd, gzip"}, 415),
    ],
)
def test_refuses_bad_or_oversized_bodies(body: bytes, headers: dict[str, str], status: int) -> None:
    limit = 32 if body == b"x" * 64 else 1 << 20
    response = _post(_echo_app(limit), body, headers)
    assert response.status_code == status
    assert response.headers["accept-encoding"] == "zstd"
    assert "detail" in response.json()


def test_request_coding_parses_header_lists() -> None:
    assert request_coding([]) is None
    assert request_coding([(b"content-encoding", b" identity ")]) is None
    assert request_coding([(b"Content-Encoding", b"ZSTD")]) == b"zstd"
    with pytest.raises(BodyCodingError):
        request_coding([(b"content-encoding", b"br")])


def test_decode_zstd_holds_the_limit_exactly() -> None:
    assert decode_zstd(zstd.compress(b"12345"), 5) == b"12345"
    with pytest.raises(BodyCodingError) as exc:
        decode_zstd(zstd.compress(b"123456"), 5)
    assert exc.value.status == 413


def test_non_http_scopes_pass_through() -> None:
    seen: list[str] = []

    async def inner(scope, receive, send) -> None:
        seen.append(scope["type"])

    middleware = ZstdRequestBodyMiddleware(inner, max_decoded_body_bytes=1)
    asyncio.run(middleware({"type": "lifespan"}, None, None))  # type: ignore[arg-type]
    assert seen == ["lifespan"]


def test_client_disconnect_while_reading_is_a_bad_request() -> None:
    sent: list[dict[str, Any]] = []

    async def inner(scope, receive, send) -> None:
        raise AssertionError("the route must not run")

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = ZstdRequestBodyMiddleware(inner, max_decoded_body_bytes=1 << 20)
    scope = {"type": "http", "headers": [(b"content-encoding", b"zstd")]}
    asyncio.run(middleware(scope, receive, send))
    assert sent[0]["status"] == 400


def test_main_app_decodes_before_the_debug_logger() -> None:
    from src.core.main import app

    assert app.user_middleware[0].cls is ZstdRequestBodyMiddleware

    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/health")

    response = asyncio.run(run())
    assert response.headers["accept-encoding"] == "zstd"
