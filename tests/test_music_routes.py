from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
import pytest

from src.core.pjsk import music


@dataclass(frozen=True)
class _RouteCase:
    endpoint: str
    native_renderer: str
    pillow_renderer: str


_ROUTE_CASES = (
    _RouteCase("music_detail", "try_render_music_detail_payload", "compose_music_detail_image"),
    _RouteCase("music_brief_list", "try_render_music_brief_list_payload", "compose_music_brief_list_image"),
    _RouteCase("music_list", "try_render_music_list_payload", "compose_music_list_image"),
    _RouteCase("music_progress", "try_render_play_progress_payload", "compose_play_progress_image"),
    _RouteCase(
        "music_rewards_detail",
        "try_render_detail_music_rewards_payload",
        "compose_detail_music_rewards_image",
    ),
    _RouteCase(
        "music_rewards_basic",
        "try_render_basic_music_rewards_payload",
        "compose_basic_music_rewards_image",
    ),
)


@pytest.mark.parametrize("case", _ROUTE_CASES, ids=lambda case: case.endpoint)
def test_music_routes_return_native_payloads(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
    request = object()
    native_payload = object()
    response = object()
    pillow_called = False

    async def native_renderer(received: Any) -> object:
        assert received is request
        return native_payload

    async def pillow_renderer(_received: Any) -> object:
        nonlocal pillow_called
        pillow_called = True
        return object()

    monkeypatch.setattr(music, case.native_renderer, native_renderer)
    monkeypatch.setattr(music, case.pillow_renderer, pillow_renderer)
    monkeypatch.setattr(
        music,
        "encoded_image_payload_to_response",
        lambda payload: response if payload is native_payload else pytest.fail("unexpected native payload"),
    )

    result = asyncio.run(getattr(music, case.endpoint)(request))

    assert result is response
    assert not pillow_called


@pytest.mark.parametrize("case", _ROUTE_CASES, ids=lambda case: case.endpoint)
def test_music_routes_fall_back_to_pillow(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
    request = object()
    image = object()
    response = object()

    async def native_renderer(received: Any) -> None:
        assert received is request
        return None

    async def pillow_renderer(received: Any) -> object:
        assert received is request
        return image

    async def image_response(received: Any) -> object:
        assert received is image
        return response

    monkeypatch.setattr(music, case.native_renderer, native_renderer)
    monkeypatch.setattr(music, case.pillow_renderer, pillow_renderer)
    monkeypatch.setattr(music, "image_to_response", image_response)

    assert asyncio.run(getattr(music, case.endpoint)(request)) is response


@pytest.mark.parametrize("case", _ROUTE_CASES, ids=lambda case: case.endpoint)
def test_music_routes_convert_renderer_errors(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
    async def failed_renderer(_request: Any) -> None:
        raise RuntimeError("render failed")

    monkeypatch.setattr(music, case.native_renderer, failed_renderer)

    with pytest.raises(HTTPException) as error:
        asyncio.run(getattr(music, case.endpoint)(object()))

    assert error.value.status_code == 500
    assert error.value.detail == "render failed"


def test_music_routes_explicitly_document_internal_errors() -> None:
    endpoint_routes = [route for route in music.router.routes if getattr(route, "methods", None) == {"POST"}]

    assert len(endpoint_routes) == len(_ROUTE_CASES)
    assert all(500 in route.responses for route in endpoint_routes)
