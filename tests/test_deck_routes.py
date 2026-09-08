from __future__ import annotations

import asyncio

from fastapi import HTTPException
import pytest

from src.core.heavy_render_pool import (
    HeavyRenderQueueFullError,
    HeavyRenderQueueTimeoutError,
    HeavyRenderTaskExecutionError,
    HeavyRenderTaskTimeoutError,
)
from src.core.pjsk import deck


class _WorkerPool:
    def __init__(self, *, result: object | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    async def render(self, kind: str, request: dict[str, object]) -> object:
        assert kind == "deck_recommend"
        assert request == {"eventId": 1}
        if self.error is not None:
            raise self.error
        return self.result


class _DeckRequest:
    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return {"eventId": 1}


def test_deck_recommend_returns_worker_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = object()
    response = object()
    monkeypatch.setattr(deck, "get_heavy_render_worker_pool", lambda: _WorkerPool(result=payload))
    monkeypatch.setattr(
        deck,
        "encoded_image_payload_to_response",
        lambda value: response if value is payload else pytest.fail("unexpected payload"),
    )

    assert asyncio.run(deck.deck_recommend(_DeckRequest())) is response


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
def test_deck_recommend_converts_worker_errors(
    error: Exception,
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deck, "get_heavy_render_worker_pool", lambda: _WorkerPool(error=error))
    monkeypatch.setattr(deck.traceback, "print_exc", lambda: None)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(deck.deck_recommend(_DeckRequest()))

    assert raised.value.status_code == status_code
    assert raised.value.detail == str(error)


def test_deck_route_explicitly_documents_worker_errors() -> None:
    route = next(route for route in deck.router.routes if getattr(route, "methods", None) == {"POST"})

    assert route.path == "/recommend"
    assert set(route.responses) == {500, 503, 504}
