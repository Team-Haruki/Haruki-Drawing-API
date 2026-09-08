from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
import pytest

from src.core.pjsk import education


@dataclass(frozen=True)
class _RouteCase:
    endpoint: str
    native_renderer: str
    pillow_renderer: str


_ROUTE_CASES = (
    _RouteCase(
        "challenge_live_detail",
        "try_render_challenge_live_detail_payload",
        "compose_challenge_live_detail_image",
    ),
    _RouteCase(
        "power_bonus_detail",
        "try_render_power_bonus_detail_payload",
        "compose_power_bonus_detail_image",
    ),
    _RouteCase(
        "area_item_materials",
        "try_render_area_item_upgrade_materials_payload",
        "compose_area_item_upgrade_materials_image",
    ),
    _RouteCase("bonds_level", "try_render_bonds_payload", "compose_bonds_image"),
    _RouteCase("leader_count", "try_render_leader_count_payload", "compose_leader_count_image"),
    _RouteCase(
        "character_mission_overview",
        "try_render_character_mission_overview_payload",
        "compose_character_mission_overview_image",
    ),
    _RouteCase(
        "character_mission_all",
        "try_render_character_mission_all_payload",
        "compose_character_mission_all_image",
    ),
)


@pytest.mark.parametrize("case", _ROUTE_CASES, ids=lambda case: case.endpoint)
def test_education_routes_return_native_payloads(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(education, case.native_renderer, native_renderer)
    monkeypatch.setattr(education, case.pillow_renderer, pillow_renderer)
    monkeypatch.setattr(
        education,
        "encoded_image_payload_to_response",
        lambda payload: response if payload is native_payload else pytest.fail("unexpected native payload"),
    )

    result = asyncio.run(getattr(education, case.endpoint)(request))

    assert result is response
    assert not pillow_called


@pytest.mark.parametrize("case", _ROUTE_CASES, ids=lambda case: case.endpoint)
def test_education_routes_fall_back_to_pillow(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(education, case.native_renderer, native_renderer)
    monkeypatch.setattr(education, case.pillow_renderer, pillow_renderer)
    monkeypatch.setattr(education, "image_to_response", image_response)

    assert asyncio.run(getattr(education, case.endpoint)(request)) is response


@pytest.mark.parametrize("case", _ROUTE_CASES, ids=lambda case: case.endpoint)
def test_education_routes_convert_renderer_errors(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
    async def failed_renderer(_request: Any) -> None:
        raise RuntimeError("render failed")

    monkeypatch.setattr(education, case.native_renderer, failed_renderer)

    with pytest.raises(HTTPException) as error:
        asyncio.run(getattr(education, case.endpoint)(object()))

    assert error.value.status_code == 500
    assert error.value.detail == "render failed"


def test_education_routes_explicitly_document_internal_errors() -> None:
    endpoint_routes = [route for route in education.router.routes if getattr(route, "methods", None) == {"POST"}]

    assert len(endpoint_routes) == len(_ROUTE_CASES)
    assert all(500 in route.responses for route in endpoint_routes)
