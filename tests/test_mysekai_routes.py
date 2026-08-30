from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
import pytest

from src.core.pjsk import mysekai


@dataclass(frozen=True)
class _RouteCase:
    endpoint: str
    native_renderer: str
    pillow_renderer: str


_ROUTE_CASES = (
    _RouteCase("mysekai_resource", "try_render_mysekai_resource_payload", "compose_mysekai_resource_image"),
    _RouteCase("mysekai_msr_map", "try_render_mysekai_msr_map_payload", "compose_mysekai_msr_map_image"),
    _RouteCase(
        "mysekai_fixture_list",
        "try_render_mysekai_fixture_list_payload",
        "compose_mysekai_fixture_list_image",
    ),
    _RouteCase(
        "mysekai_fixture_detail",
        "try_render_mysekai_fixture_detail_payload",
        "compose_mysekai_fixture_detail_image",
    ),
    _RouteCase(
        "mysekai_door_upgrade",
        "try_render_mysekai_door_upgrade_payload",
        "compose_mysekai_door_upgrade_image",
    ),
    _RouteCase(
        "mysekai_music_record",
        "try_render_mysekai_musicrecord_payload",
        "compose_mysekai_musicrecord_image",
    ),
    _RouteCase(
        "mysekai_talk_list",
        "try_render_mysekai_talk_list_payload",
        "compose_mysekai_talk_list_image",
    ),
    _RouteCase(
        "mysekai_housing_competition",
        "try_render_mysekai_housing_competition_payload",
        "compose_mysekai_housing_competition_image",
    ),
)


@pytest.mark.parametrize("case", _ROUTE_CASES, ids=lambda case: case.endpoint)
def test_mysekai_routes_return_native_payloads(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(mysekai, case.native_renderer, native_renderer)
    monkeypatch.setattr(mysekai, case.pillow_renderer, pillow_renderer)
    monkeypatch.setattr(
        mysekai,
        "encoded_image_payload_to_response",
        lambda payload: response if payload is native_payload else pytest.fail("unexpected native payload"),
    )

    result = asyncio.run(getattr(mysekai, case.endpoint)(request))

    assert result is response
    assert not pillow_called


@pytest.mark.parametrize("case", _ROUTE_CASES, ids=lambda case: case.endpoint)
def test_mysekai_routes_fall_back_to_pillow(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
    request = object()
    image = SimpleNamespace(width=320, height=180)
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

    monkeypatch.setattr(mysekai, case.native_renderer, native_renderer)
    monkeypatch.setattr(mysekai, case.pillow_renderer, pillow_renderer)
    monkeypatch.setattr(mysekai, "image_to_response", image_response)

    assert asyncio.run(getattr(mysekai, case.endpoint)(request)) is response


@pytest.mark.parametrize("case", _ROUTE_CASES, ids=lambda case: case.endpoint)
def test_mysekai_routes_convert_renderer_errors(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
    async def failed_renderer(_request: Any) -> None:
        raise RuntimeError("render failed")

    monkeypatch.setattr(mysekai, case.native_renderer, failed_renderer)

    with pytest.raises(HTTPException) as error:
        asyncio.run(getattr(mysekai, case.endpoint)(object()))

    assert error.value.status_code == 500
    assert error.value.detail == "render failed"


def test_mysekai_routes_explicitly_document_internal_errors() -> None:
    endpoint_routes = [route for route in mysekai.router.routes if getattr(route, "methods", None) == {"POST"}]

    assert len(endpoint_routes) == len(_ROUTE_CASES)
    assert all(500 in route.responses for route in endpoint_routes)
