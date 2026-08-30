from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from fastapi import HTTPException
import pytest

from src.core.pjsk import event, score


@dataclass(frozen=True)
class _RouteCase:
    module: ModuleType
    endpoint: str
    native_renderer: str
    pillow_renderer: str


_ROUTE_CASES = (
    _RouteCase(event, "event_detail", "try_render_event_detail_payload", "compose_event_detail_image"),
    _RouteCase(event, "event_record", "try_render_event_record_payload", "compose_event_record_image"),
    _RouteCase(event, "event_list", "try_render_event_list_payload", "compose_event_list_image"),
    _RouteCase(event, "event_planner", "try_render_event_planner_payload", "compose_event_planner_image"),
    _RouteCase(score, "score_control", "try_render_score_control_payload", "compose_score_control_image"),
    _RouteCase(
        score,
        "custom_room_score_control",
        "try_render_custom_room_score_control_payload",
        "compose_custom_room_score_control_image",
    ),
    _RouteCase(score, "music_meta", "try_render_music_meta_payload", "compose_music_meta_image"),
    _RouteCase(score, "music_board", "try_render_music_board_payload", "compose_music_board_image"),
)


@pytest.mark.parametrize("case", _ROUTE_CASES, ids=lambda case: case.endpoint)
def test_event_and_score_routes_return_native_payloads(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
    request = object()
    payload = object()
    response = object()
    pillow_called = False

    async def native_renderer(received: Any) -> object:
        assert received is request
        return payload

    async def pillow_renderer(_received: Any) -> object:
        nonlocal pillow_called
        pillow_called = True
        return object()

    monkeypatch.setattr(case.module, case.native_renderer, native_renderer)
    monkeypatch.setattr(case.module, case.pillow_renderer, pillow_renderer)
    monkeypatch.setattr(
        case.module,
        "encoded_image_payload_to_response",
        lambda value: response if value is payload else pytest.fail("unexpected payload"),
    )

    assert asyncio.run(getattr(case.module, case.endpoint)(request)) is response
    assert not pillow_called


@pytest.mark.parametrize("case", _ROUTE_CASES, ids=lambda case: case.endpoint)
def test_event_and_score_routes_fall_back_to_pillow(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(case.module, case.native_renderer, native_renderer)
    monkeypatch.setattr(case.module, case.pillow_renderer, pillow_renderer)
    monkeypatch.setattr(case.module, "image_to_response", image_response)

    assert asyncio.run(getattr(case.module, case.endpoint)(request)) is response


@pytest.mark.parametrize("case", _ROUTE_CASES, ids=lambda case: case.endpoint)
def test_event_and_score_routes_convert_renderer_errors(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
    async def failed_renderer(_request: Any) -> None:
        raise RuntimeError("render failed")

    monkeypatch.setattr(case.module, case.native_renderer, failed_renderer)
    if case.module is event:
        monkeypatch.setattr(event.traceback, "print_exc", lambda: None)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(getattr(case.module, case.endpoint)(object()))

    assert raised.value.status_code == 500
    assert raised.value.detail == "render failed"


@pytest.mark.parametrize("module", [event, score], ids=lambda module: module.__name__.rsplit(".", 1)[-1])
def test_event_and_score_routes_explicitly_document_internal_errors(module: ModuleType) -> None:
    endpoint_routes = [route for route in module.router.routes if getattr(route, "methods", None) == {"POST"}]

    assert len(endpoint_routes) == 4
    assert all(500 in route.responses for route in endpoint_routes)
