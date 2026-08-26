from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import httpx

from src.core.debug import get_http_request_stats, install_debug_middleware, reset_http_request_stats


async def _exercise_app() -> None:
    app = FastAPI()
    install_debug_middleware(app)

    @app.get("/ok/{item_id}")
    async def ok(item_id: str):
        return {"item": item_id}

    @app.get("/fail/{item_id}")
    async def fail(item_id: str):
        return JSONResponse(status_code=500, content={"status": "failed", "item": item_id})

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/ok/private-user-value")).status_code == 200
        assert (await client.get("/fail/private-user-value")).status_code == 500


def test_http_request_stats_are_aggregate_and_route_template_only() -> None:
    reset_http_request_stats()
    asyncio.run(_exercise_app())

    stats = get_http_request_stats()
    assert stats == {
        "total": 2,
        "status_families": {"1xx": 0, "2xx": 1, "3xx": 0, "4xx": 0, "5xx": 1, "other": 0},
        "status_codes": {"200": 1, "500": 1},
        "server_errors": {
            "total": 1,
            "uncaught_exceptions": 0,
            "by_route": {"/fail/{item_id}": 1},
        },
    }
    assert "private-user-value" not in json.dumps(stats)
    reset_http_request_stats()
