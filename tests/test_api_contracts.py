import asyncio

import httpx
import pytest

from src.core import health
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


def test_readiness_endpoint_contract():
    response = asyncio.run(_request("GET", "/ready"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["reasons"] == []
    assert "metrics" in payload
    assert "thresholds" in payload


def test_readiness_endpoint_reports_unhealthy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        health,
        "evaluate_runtime_readiness",
        lambda: (
            False,
            ["inflight 99 >= 64"],
            {"inflight": 99, "rss_mb": 1234, "asyncio_tasks": 77},
        ),
    )

    response = asyncio.run(_request("GET", "/ready"))

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["reasons"] == ["inflight 99 >= 64"]


def test_validation_errors_are_reported_before_rendering():
    response = asyncio.run(_request("POST", "/api/pjsk/sk/query", json={"id": 123}))

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")


def test_mysekai_housing_competition_endpoint_contract():
    tiny_png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    response = asyncio.run(
        _request(
            "POST",
            "/api/pjsk/mysekai/housing-competition",
            json={
                "competition_id": 25,
                "region": "jp",
                "name": "烤森百景",
                "description": "百景投稿列表",
                "banner_image_base64": tiny_png,
                "sample_count": 2,
                "unique_count": 3,
                "entries": [
                    {
                        "rank": 1,
                        "review_count": 34,
                        "owner_user_name": "Tester",
                        "name": "海边小屋",
                        "word": "欢迎参观",
                        "thumbnail_image_base64": tiny_png,
                        "next_review_count": 33,
                        "next_delta": 1,
                    }
                ],
            },
        )
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content.startswith(b"\x89PNG")
