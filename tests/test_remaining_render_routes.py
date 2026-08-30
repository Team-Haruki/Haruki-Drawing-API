from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace
from typing import Any

from fastapi import HTTPException
import pytest

from src.core.pjsk import card, chart, profile
from src.sekai.chart import drawer as chart_drawer


@dataclass(frozen=True)
class _RouteCase:
    module: ModuleType
    endpoint: str
    native_renderer: str
    pillow_renderer: str


_ROUTE_CASES = (
    _RouteCase(card, "card_detail", "try_render_card_detail_payload", "compose_card_detail_image"),
    _RouteCase(card, "card_list", "try_render_card_list_payload", "compose_card_list_image"),
    _RouteCase(card, "card_box", "try_render_box_payload", "compose_box_image"),
    _RouteCase(profile, "profile", "try_render_profile_payload", "compose_profile_image"),
)


def _request(case: _RouteCase) -> SimpleNamespace:
    if case.module is profile:
        return SimpleNamespace(profile=None, honors=[])
    return SimpleNamespace(cards=[object()])


def _patch_side_effects(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
    if case.module is profile:
        monkeypatch.setattr(profile, "set_request_stage", lambda _stage: None)


@pytest.mark.parametrize("case", _ROUTE_CASES, ids=lambda case: case.endpoint)
def test_remaining_routes_return_native_payloads(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(case)
    payload = SimpleNamespace(encode_elapsed=0.1, image_width=10, image_height=20)
    response = object()
    pillow_called = False

    async def native_renderer(received: Any) -> object:
        assert received is request
        return payload

    async def pillow_renderer(_received: Any) -> object:
        nonlocal pillow_called
        pillow_called = True
        return object()

    _patch_side_effects(case, monkeypatch)
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
def test_remaining_routes_fall_back_to_pillow(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(case)
    image = SimpleNamespace(width=10, height=20)
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

    _patch_side_effects(case, monkeypatch)
    monkeypatch.setattr(case.module, case.native_renderer, native_renderer)
    monkeypatch.setattr(case.module, case.pillow_renderer, pillow_renderer)
    monkeypatch.setattr(case.module, "image_to_response", image_response)

    assert asyncio.run(getattr(case.module, case.endpoint)(request)) is response


@pytest.mark.parametrize("case", _ROUTE_CASES, ids=lambda case: case.endpoint)
def test_remaining_routes_convert_renderer_errors(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
    async def failed_renderer(_request: Any) -> None:
        raise RuntimeError("render failed")

    _patch_side_effects(case, monkeypatch)
    monkeypatch.setattr(case.module, case.native_renderer, failed_renderer)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(getattr(case.module, case.endpoint)(_request(case)))

    assert raised.value.status_code == 500
    assert raised.value.detail == "render failed"


def test_chart_returns_native_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    request = object()
    payload = object()
    response = object()

    async def native_renderer(received: Any) -> object:
        assert received is request
        return payload

    monkeypatch.setattr(chart_drawer, "try_render_music_chart_payload", native_renderer)
    monkeypatch.setattr(
        chart,
        "encoded_image_payload_to_response",
        lambda value: response if value is payload else pytest.fail("unexpected payload"),
    )

    assert asyncio.run(chart.music_chart(request)) is response


def test_chart_falls_back_to_png(monkeypatch: pytest.MonkeyPatch) -> None:
    request = object()
    image = object()
    response = object()

    async def native_renderer(_request: Any) -> None:
        return None

    async def pillow_renderer(received: Any) -> object:
        assert received is request
        return image

    async def image_response(received: Any, **kwargs: Any) -> object:
        assert received is image
        assert kwargs == {"export_format": "png"}
        return response

    monkeypatch.setattr(chart_drawer, "try_render_music_chart_payload", native_renderer)
    monkeypatch.setattr(chart_drawer, "compose_music_chart_image", pillow_renderer)
    monkeypatch.setattr(chart, "image_to_response", image_response)

    assert asyncio.run(chart.music_chart(request)) is response


def test_chart_converts_renderer_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failed_renderer(_request: Any) -> None:
        raise RuntimeError("render failed")

    monkeypatch.setattr(chart_drawer, "try_render_music_chart_payload", failed_renderer)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(chart.music_chart(object()))

    assert raised.value.status_code == 500
    assert raised.value.detail == "render failed"
    assert isinstance(raised.value.__cause__, RuntimeError)


def test_custom_profile_converts_pre_attempt_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def failed_validation(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("validation failed")

    monkeypatch.setattr(profile, "validate_custom_profile_card", failed_validation)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(profile.custom_profile_card(SimpleNamespace(card={})))

    assert raised.value.status_code == 500
    assert raised.value.detail == "validation failed"


def test_remaining_routes_explicitly_document_errors() -> None:
    card_routes = [route for route in card.router.routes if getattr(route, "methods", None) == {"POST"}]
    chart_routes = [route for route in chart.router.routes if getattr(route, "methods", None) == {"POST"}]
    profile_routes = {
        route.path: route for route in profile.router.routes if getattr(route, "methods", None) == {"POST"}
    }

    assert len(card_routes) == 3
    assert all(500 in route.responses for route in card_routes)
    assert len(chart_routes) == 1
    assert 500 in chart_routes[0].responses
    assert 500 in profile_routes[""].responses
    assert {400, 500} <= profile_routes["/custom-profile-card"].responses.keys()
