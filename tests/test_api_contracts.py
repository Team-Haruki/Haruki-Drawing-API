import asyncio

import httpx

from src.core.main import app


async def _request(method: str, url: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, url, **kwargs)


def test_health_endpoint_contract():
    response = asyncio.run(_request("GET", "/health"))

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_cache_stats_endpoint_contract():
    response = asyncio.run(_request("GET", "/cache/stats"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"
    assert "image_cache" in payload["caches"]
    assert "thumbnail_cache" in payload["caches"]
    assert "composed_image_cache" in payload["caches"]


def test_validation_errors_are_reported_before_rendering():
    response = asyncio.run(_request("POST", "/api/pjsk/sk/query", json={"id": 123}))

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
