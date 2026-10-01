"""zstd request bodies, negotiated per server (RFC 7694).

Every response advertises ``Accept-Encoding: zstd``; Haruki-Cloud only sends a
``Content-Encoding: zstd`` render request to a node that has advertised it, so
old and new nodes mix safely during a rollout. The body is decoded here, before
any other middleware or route reads it: the debug logger, the render-cache
directive and every renderer see the plain JSON. The decoded body is held to
``settings.server.max_decoded_body_bytes`` (as is the compressed one); a frame
that expands past it is refused with 413 without materialising the rest.

Responses are images (already compressed) or small JSON artifact refs, so they
are not encoded.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
import json
from typing import Any

from compression import zstd

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

ZSTD = b"zstd"
_ADVERTISEMENT = (b"accept-encoding", ZSTD)


class BodyCodingError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def request_coding(headers: list[tuple[bytes, bytes]]) -> bytes | None:
    """The request's content coding (lowercased), ``None`` for identity."""
    codings: list[bytes] = []
    for name, value in headers:
        if name.lower() == b"content-encoding":
            codings.extend(c.strip().lower() for c in value.split(b",") if c.strip())
    codings = [c for c in codings if c != b"identity"]
    if not codings:
        return None
    if codings == [ZSTD]:
        return ZSTD
    raise BodyCodingError(415, "unsupported Content-Encoding; this server accepts zstd")


def decode_zstd(data: bytes, limit: int) -> bytes:
    """Decode a zstd body to at most ``limit`` bytes."""
    decompressor = zstd.ZstdDecompressor()
    try:
        decoded = decompressor.decompress(data, max_length=limit + 1)
    except zstd.ZstdError as exc:
        raise BodyCodingError(400, f"invalid zstd body: {exc}") from exc
    if len(decoded) > limit or (not decompressor.eof and not decompressor.needs_input):
        raise BodyCodingError(413, "decompressed request body exceeds byte limit")
    if not decompressor.eof:
        raise BodyCodingError(400, "invalid zstd body: truncated frame")
    if decompressor.unused_data:
        raise BodyCodingError(400, "invalid zstd body: data after the frame")
    return decoded


async def _read_body(receive: Receive, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise BodyCodingError(400, "client disconnected")
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            raise BodyCodingError(413, "request body exceeds byte limit")
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


async def _error(send: Send, status: int, message: str) -> None:
    body = json.dumps({"detail": message}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                _ADVERTISEMENT,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class ZstdRequestBodyMiddleware:
    """ASGI middleware: decode zstd request bodies, advertise zstd on responses."""

    def __init__(self, app: ASGIApp, max_decoded_body_bytes: int) -> None:
        self.app = app
        self.limit = max_decoded_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def advertise(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [h for h in message.get("headers", []) if h[0].lower() != b"accept-encoding"]
                headers.append(_ADVERTISEMENT)
                message["headers"] = headers
            await send(message)

        try:
            coding = request_coding(scope.get("headers", []))
        except BodyCodingError as exc:
            await _error(send, exc.status, exc.message)
            return
        if coding is None:
            await self.app(scope, receive, advertise)
            return

        try:
            compressed = await _read_body(receive, self.limit)
            decoded = decode_zstd(compressed, self.limit)
        except BodyCodingError as exc:
            await _error(send, exc.status, exc.message)
            return

        headers = [
            (name, value)
            for name, value in scope.get("headers", [])
            if name.lower() not in (b"content-encoding", b"content-length")
        ]
        headers.append((b"content-length", str(len(decoded)).encode()))
        scope = dict(scope)
        scope["headers"] = headers
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": decoded, "more_body": False}
            return await receive()

        await self.app(scope, replay, advertise)
