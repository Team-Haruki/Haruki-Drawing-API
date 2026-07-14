"""An image response must leave as ONE body message.

This pins the fix for a bug that cost the service 32x its throughput on every drawing endpoint.
The responses were built as ``StreamingResponse(io.BytesIO(image_bytes))``. That streams nothing --
the bytes are already whole in memory -- but it does hand Starlette a *sync* iterable, and Starlette
drives those through ``iterate_in_threadpool``. Iterating a ``BytesIO`` yields **lines**, so a
binary PNG got split on every 0x0A byte: ~384-byte chunks, thousands of thread-pool round-trips and
thousands of ASGI body messages per image. The server finished a deck render in 0.12s and the client
waited ~10s for it, with the CPU 95% idle.

The trap is that the broken version is *correct* -- the client still receives every byte. Only the
message count betrays it, so that is what these tests assert.
"""

from __future__ import annotations

import asyncio

from src.core.heavy_render_pool import EncodedImagePayload
from src.core.utils import encoded_image_payload_to_response

# 0x0A is what BytesIO would have split on. A real PNG carries one roughly every 256 bytes.
PNG_LIKE = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8


def _drive_asgi(response) -> list[dict]:
    """Collect the ASGI messages the response actually emits."""
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.disconnect"}

    scope = {"type": "http", "method": "POST", "path": "/x", "headers": []}
    asyncio.run(response(scope, receive, send))
    return sent


def _payload() -> EncodedImagePayload:
    return EncodedImagePayload(
        image_bytes=PNG_LIKE,
        media_type="image/png",
        filename="x.png",
        image_width=4,
        image_height=4,
        image_mode="RGBA",
        encode_elapsed=0.0,
    )


def test_the_image_leaves_in_a_single_body_message():
    sent = _drive_asgi(encoded_image_payload_to_response(_payload()))
    bodies = [m for m in sent if m["type"] == "http.response.body"]
    assert len(bodies) == 1, (
        f"{len(bodies)} body messages for a {len(PNG_LIKE)}-byte image — the response is chunking "
        "again (BytesIO iterates by LINE, so a PNG splits on every 0x0A byte)"
    )
    assert bodies[0]["body"] == PNG_LIKE


def test_the_body_is_byte_identical_and_length_is_declared():
    sent = _drive_asgi(encoded_image_payload_to_response(_payload()))
    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}

    assert headers["content-length"] == str(len(PNG_LIKE)), (
        "no Content-Length means chunked transfer-encoding: the client cannot size the image"
    )
    assert headers["content-type"] == "image/png"
    assert "x.png" in headers["content-disposition"]


def test_a_newline_heavy_image_is_not_split():
    """The regression in its purest form: bytes that are *nothing but* line breaks."""
    payload = _payload()
    payload.image_bytes = b"\n" * 4096
    sent = _drive_asgi(encoded_image_payload_to_response(payload))
    bodies = [m for m in sent if m["type"] == "http.response.body"]

    assert len(bodies) == 1, f"4096 newlines became {len(bodies)} body messages"
    assert bodies[0]["body"] == b"\n" * 4096
