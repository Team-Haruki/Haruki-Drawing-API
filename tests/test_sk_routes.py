from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
import pytest

from src.core.pjsk import sk


@dataclass(frozen=True)
class _RouteCase:
    endpoint: str
    native_renderer: str
    pillow_renderer: str


_ROUTE_CASES = (
    _RouteCase("sk_line", "try_render_skl_payload", "compose_skl_image"),
    _RouteCase("sk_query", "try_render_sk_payload", "compose_sk_image"),
    _RouteCase("sk_check_room", "try_render_cf_payload", "compose_cf_image"),
    _RouteCase("sk_csb", "try_render_csb_payload", "compose_csb_image"),
    _RouteCase("sk_speed", "try_render_sks_payload", "compose_sks_image"),
    _RouteCase("sk_player_trace", "try_render_player_trace_payload", "compose_player_trace_image"),
    _RouteCase("sk_rank_trace", "try_render_rank_trace_payload", "compose_rank_trace_image"),
    _RouteCase("sk_winrate", "try_render_winrate_predict_payload", "compose_winrate_predict_image"),
)


@pytest.mark.parametrize("case", _ROUTE_CASES, ids=lambda case: case.endpoint)
def test_sk_routes_return_native_payloads(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(sk, case.native_renderer, native_renderer)
    monkeypatch.setattr(sk, case.pillow_renderer, pillow_renderer)
    monkeypatch.setattr(
        sk,
        "encoded_image_payload_to_response",
        lambda payload: response if payload is native_payload else pytest.fail("unexpected native payload"),
    )

    result = asyncio.run(getattr(sk, case.endpoint)(request))

    assert result is response
    assert not pillow_called


@pytest.mark.parametrize("case", _ROUTE_CASES, ids=lambda case: case.endpoint)
def test_sk_routes_fall_back_to_pillow(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(sk, case.native_renderer, native_renderer)
    monkeypatch.setattr(sk, case.pillow_renderer, pillow_renderer)
    monkeypatch.setattr(sk, "image_to_response", image_response)

    assert asyncio.run(getattr(sk, case.endpoint)(request)) is response


@pytest.mark.parametrize("case", _ROUTE_CASES, ids=lambda case: case.endpoint)
def test_sk_routes_convert_renderer_errors(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
    async def failed_renderer(_request: Any) -> None:
        raise RuntimeError("render failed")

    monkeypatch.setattr(sk, case.native_renderer, failed_renderer)

    with pytest.raises(HTTPException) as error:
        asyncio.run(getattr(sk, case.endpoint)(object()))

    assert error.value.status_code == 500
    assert error.value.detail == "render failed"


def test_sk_routes_explicitly_document_internal_errors() -> None:
    endpoint_routes = [route for route in sk.router.routes if getattr(route, "methods", None) == {"POST"}]

    assert len(endpoint_routes) == len(_ROUTE_CASES)
    assert all(500 in route.responses for route in endpoint_routes)
