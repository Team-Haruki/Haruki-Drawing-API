from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from fastapi import HTTPException
import pytest

from src.core.pjsk import command_help, costume, gacha, honor, inventory, stamp, vlive


@dataclass(frozen=True)
class _RouteCase:
    module: ModuleType
    endpoint: str
    native_renderer: str
    pillow_renderer: str


_STANDARD_CASES = (
    _RouteCase(command_help, "command_help", "try_render_command_help_payload", "compose_command_help_image"),
    _RouteCase(costume, "costume_list", "try_render_costume_list_payload", "compose_costume_list_image"),
    _RouteCase(costume, "costume_detail", "try_render_costume_detail_payload", "compose_costume_detail_image"),
    _RouteCase(gacha, "gacha_list", "try_render_gacha_list_payload", "compose_gacha_list_image"),
    _RouteCase(gacha, "gacha_detail", "try_render_gacha_detail_payload", "compose_gacha_detail_image"),
    _RouteCase(inventory, "inventory_list", "try_render_inventory_list_payload", "compose_inventory_list_image"),
    _RouteCase(stamp, "stamp_list", "try_render_stamp_payload", "compose_stamp_list_image"),
    _RouteCase(vlive, "vlive_list", "try_render_vlive_list_payload", "compose_vlive_list_image"),
)


def _patch_side_effects(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
    if case.module is command_help:
        monkeypatch.setattr(command_help, "set_request_stage", lambda _stage: None)
    elif case.module is vlive:
        monkeypatch.setattr(vlive.traceback, "print_exc", lambda: None)


@pytest.mark.parametrize("case", _STANDARD_CASES, ids=lambda case: case.endpoint)
def test_small_routes_return_native_payloads(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
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


@pytest.mark.parametrize("case", _STANDARD_CASES, ids=lambda case: case.endpoint)
def test_small_routes_fall_back_to_pillow(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
    request = object()
    image = object()
    response = object()

    async def native_renderer(received: Any) -> None:
        assert received is request
        return None

    async def pillow_renderer(received: Any) -> object:
        assert received is request
        return image

    async def image_response(received: Any, **kwargs: Any) -> object:
        assert received is image
        if case.module is command_help:
            assert kwargs == {"export_format": "png"}
        else:
            assert not kwargs
        return response

    _patch_side_effects(case, monkeypatch)
    monkeypatch.setattr(case.module, case.native_renderer, native_renderer)
    monkeypatch.setattr(case.module, case.pillow_renderer, pillow_renderer)
    monkeypatch.setattr(case.module, "image_to_response", image_response)

    assert asyncio.run(getattr(case.module, case.endpoint)(request)) is response


@pytest.mark.parametrize("case", _STANDARD_CASES, ids=lambda case: case.endpoint)
def test_small_routes_convert_renderer_errors(case: _RouteCase, monkeypatch: pytest.MonkeyPatch) -> None:
    async def failed_renderer(_request: Any) -> None:
        raise RuntimeError("render failed")

    _patch_side_effects(case, monkeypatch)
    monkeypatch.setattr(case.module, case.native_renderer, failed_renderer)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(getattr(case.module, case.endpoint)(object()))

    assert raised.value.status_code == 500
    assert raised.value.detail == "render failed"


def test_honor_returns_native_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    request = object()
    payload = object()
    response = object()

    async def native_renderer(received: Any) -> object:
        assert received is request
        return payload

    monkeypatch.setattr(honor, "try_render_full_honor_payload", native_renderer)
    monkeypatch.setattr(
        honor,
        "encoded_image_payload_to_response",
        lambda value: response if value is payload else pytest.fail("unexpected payload"),
    )

    assert asyncio.run(honor.honor(request)) is response


def test_honor_falls_back_to_pillow(monkeypatch: pytest.MonkeyPatch) -> None:
    request = object()
    image = object()
    watermarked = object()
    response = object()

    async def native_renderer(received: Any) -> None:
        assert received is request
        return None

    async def pillow_renderer(received: Any) -> object:
        assert received is request
        return image

    async def watermark(received_image: Any, received_request: Any) -> object:
        assert received_image is image
        assert received_request is request
        return watermarked

    async def image_response(received: Any) -> object:
        assert received is watermarked
        return response

    monkeypatch.setattr(honor, "try_render_full_honor_payload", native_renderer)
    monkeypatch.setattr(honor, "compose_full_honor_image", pillow_renderer)
    monkeypatch.setattr(honor, "add_request_watermark_to_image", watermark)
    monkeypatch.setattr(honor, "image_to_response", image_response)

    assert asyncio.run(honor.honor(request)) is response


def test_honor_converts_renderer_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failed_renderer(_request: Any) -> None:
        raise RuntimeError("render failed")

    monkeypatch.setattr(honor, "try_render_full_honor_payload", failed_renderer)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(honor.honor(object()))

    assert raised.value.status_code == 500
    assert raised.value.detail == "render failed"


@pytest.mark.parametrize(
    "module",
    [command_help, costume, gacha, honor, inventory, stamp, vlive],
    ids=lambda module: module.__name__.rsplit(".", 1)[-1],
)
def test_small_routes_explicitly_document_internal_errors(module: ModuleType) -> None:
    endpoint_routes = [route for route in module.router.routes if getattr(route, "methods", None) == {"POST"}]

    assert endpoint_routes
    assert all(500 in route.responses for route in endpoint_routes)
