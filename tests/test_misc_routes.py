from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException
import pytest

from src.core.heavy_render_pool import (
    HeavyRenderQueueFullError,
    HeavyRenderQueueTimeoutError,
    HeavyRenderTaskExecutionError,
    HeavyRenderTaskTimeoutError,
)
from src.core.pjsk import misc


class _WorkerPool:
    def __init__(self, *, result: object | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    async def render(self, kind: str, request: dict[str, object]) -> object:
        assert kind == "chara_birthday"
        assert request == {"characterId": 1}
        if self.error is not None:
            raise self.error
        return self.result


class _BirthdayRequest:
    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return {"characterId": 1}


def test_chara_birthday_returns_worker_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = object()
    response = object()
    monkeypatch.setattr(misc, "get_heavy_render_worker_pool", lambda: _WorkerPool(result=payload))
    monkeypatch.setattr(
        misc,
        "encoded_image_payload_to_response",
        lambda value: response if value is payload else pytest.fail("unexpected payload"),
    )

    assert asyncio.run(misc.chara_birthday(_BirthdayRequest())) is response


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (HeavyRenderQueueFullError("full"), 503),
        (HeavyRenderQueueTimeoutError("queue timeout"), 503),
        (HeavyRenderTaskTimeoutError("task timeout"), 504),
        (HeavyRenderTaskExecutionError("task failed"), 500),
        (RuntimeError("unexpected"), 500),
    ],
)
def test_chara_birthday_converts_worker_errors(
    error: Exception,
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(misc, "get_heavy_render_worker_pool", lambda: _WorkerPool(error=error))

    with pytest.raises(HTTPException) as raised:
        asyncio.run(misc.chara_birthday(_BirthdayRequest()))

    assert raised.value.status_code == status_code
    assert raised.value.detail == str(error)


def test_alias_list_returns_native_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    request = object()
    payload = object()
    response = object()

    async def native_renderer(received: Any) -> object:
        assert received is request
        return payload

    monkeypatch.setattr(misc, "try_render_alias_list_payload", native_renderer)
    monkeypatch.setattr(
        misc,
        "encoded_image_payload_to_response",
        lambda value: response if value is payload else pytest.fail("unexpected payload"),
    )

    assert asyncio.run(misc.alias_list(request)) is response


def test_alias_list_falls_back_to_pillow(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(misc, "try_render_alias_list_payload", native_renderer)
    monkeypatch.setattr(misc, "compose_alias_list_image", pillow_renderer)
    monkeypatch.setattr(misc, "image_to_response", image_response)

    assert asyncio.run(misc.alias_list(request)) is response


def test_alias_list_converts_renderer_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failed_renderer(_request: Any) -> None:
        raise RuntimeError("render failed")

    monkeypatch.setattr(misc, "try_render_alias_list_payload", failed_renderer)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(misc.alias_list(object()))

    assert raised.value.status_code == 500
    assert raised.value.detail == "render failed"


def test_misc_routes_explicitly_document_errors() -> None:
    route_responses = {
        route.path: set(route.responses) for route in misc.router.routes if getattr(route, "methods", None) == {"POST"}
    }

    assert route_responses == {
        "/chara-birthday": {500, 503, 504},
        "/alias-list": {500},
    }
