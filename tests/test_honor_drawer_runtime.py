from types import SimpleNamespace

from PIL import Image
import pytest

from src.sekai.honor import drawer
from src.sekai.honor.model import HonorRequest


@pytest.mark.anyio
async def test_load_honor_images_resolves_required_skips_missing_optional_and_empty(monkeypatch) -> None:
    specs = [
        SimpleNamespace(path_field="honor_img_path", image_key="honor_img", on_supplied_missing="raise"),
        SimpleNamespace(path_field="rank_img_path", image_key="rank_img", on_supplied_missing="ignore"),
        SimpleNamespace(path_field="word_img_path", image_key="word_img", on_supplied_missing="ignore"),
    ]
    monkeypatch.setattr(drawer, "honor_asset_specs", lambda _request: specs)

    async def load(_base, path, *, on_missing):
        assert on_missing == "raise"
        if path == "missing.png":
            raise FileNotFoundError(path)
        return f"ref:{path}"

    monkeypatch.setattr(drawer, "get_asset_image_ref", load)
    request = HonorRequest(honor_img_path="honor.png", rank_img_path="missing.png")
    assert await drawer.load_honor_images(request) == {"honor_img": "ref:honor.png", "rank_img": None}

    monkeypatch.setattr(drawer, "honor_asset_specs", lambda _request: [])
    assert await drawer.load_honor_images(HonorRequest()) == {}


def test_build_full_honor_cache_key_includes_payload_and_all_asset_signatures(monkeypatch) -> None:
    request = HonorRequest(honor_img_path="honor.png", rank_img_path="rank.png", timezone="Asia/Tokyo")
    monkeypatch.setattr(
        drawer,
        "HONOR_ASSET_MANIFEST",
        {"honor_img": "honor_img_path", "rank_img": "rank_img_path"},
    )
    monkeypatch.setattr(drawer, "get_image_asset_signature", lambda _base, path: (path, 1))
    captured = {}

    def build(namespace, payload, *, asset_signatures):
        captured.update(namespace=namespace, payload=payload, signatures=asset_signatures)
        return "cache-key"

    monkeypatch.setattr(drawer, "build_rendered_image_cache_key", build)
    assert drawer.build_full_honor_cache_key(request) == "cache-key"
    assert captured["namespace"] == "full_honor_image"
    assert "timezone" not in captured["payload"]
    assert captured["signatures"] == {"honor_img": ("honor.png", 1), "rank_img": ("rank.png", 1)}


@pytest.mark.anyio
async def test_build_canvas_and_compose_honor_cover_cache_none_and_store(monkeypatch) -> None:
    request = HonorRequest()
    image = Image.new("RGBA", (2, 2), "red")
    monkeypatch.setattr(drawer, "load_honor_images", lambda _request: _async_value({"honor_img": image}))
    monkeypatch.setattr(drawer, "build_honor_badge_canvas", lambda rqd, images: (rqd, images))
    canvas_data = await drawer.build_honor_badge_canvas_from_request(request)
    assert canvas_data == (request, {"honor_img": image})

    monkeypatch.setattr(drawer, "build_full_honor_cache_key", lambda _request: "key")
    monkeypatch.setattr(drawer, "get_composed_image_cached", lambda _key: image)
    assert await drawer.compose_full_honor_image(request) is image

    monkeypatch.setattr(drawer, "get_composed_image_cached", lambda _key: None)
    monkeypatch.setattr(drawer, "build_honor_badge_canvas_from_request", lambda _request: _async_value(None))
    assert await drawer.compose_full_honor_image(request) is None

    canvas = SimpleNamespace(get_img_sync=lambda: image)
    monkeypatch.setattr(drawer, "build_honor_badge_canvas_from_request", lambda _request: _async_value(canvas))
    monkeypatch.setattr(drawer, "run_in_pool", lambda func: _async_value(func()))
    stored = []
    monkeypatch.setattr(drawer, "put_composed_image_cache", lambda key, value: stored.append((key, value)))
    assert await drawer.compose_full_honor_image(request) is image
    assert stored == [("key", image)]


@pytest.mark.anyio
async def test_compose_honor_does_not_cache_none_result(monkeypatch) -> None:
    request = HonorRequest()
    canvas = SimpleNamespace(get_img_sync=lambda: None)
    monkeypatch.setattr(drawer, "build_full_honor_cache_key", lambda _request: "key")
    monkeypatch.setattr(drawer, "get_composed_image_cached", lambda _key: None)
    monkeypatch.setattr(drawer, "build_honor_badge_canvas_from_request", lambda _request: _async_value(canvas))
    monkeypatch.setattr(drawer, "run_in_pool", lambda func: _async_value(func()))
    stored = []
    monkeypatch.setattr(drawer, "put_composed_image_cache", lambda *args: stored.append(args))
    assert await drawer.compose_full_honor_image(request) is None
    assert stored == []


async def _async_value(value):
    return value
