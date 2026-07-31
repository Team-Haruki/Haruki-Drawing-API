from __future__ import annotations

import asyncio
import json
from pathlib import Path

from PIL import Image
import pytest

from src.sekai.base.plot import Canvas, CanvasImageBox, ImageBox
from src.sekai.base.utils import get_asset_image_ref
from src.sekai.honor.model import HonorRequest
from src.sekai.profile import drawer as profile_drawer
from src.sekai.profile.model import ProfileRequest
from src.sekai.skia_renderer.canvas import build_canvas_ir

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PAYLOAD = REPO_ROOT / "out" / "parity-payloads" / "profile.json"


def _walk_nodes(nodes):
    for node in nodes:
        yield node
        yield from _walk_nodes(node.get("children", ()))


@pytest.mark.skipif(not PROFILE_PAYLOAD.is_file(), reason="profile parity fixture not present")
def test_profile_honors_lower_as_asset_backed_raster_subscenes() -> None:
    request = ProfileRequest.model_validate(json.loads(PROFILE_PAYLOAD.read_text(encoding="utf-8")))
    canvas = asyncio.run(profile_drawer._build_profile_canvas(request))
    builder, mem_images = build_canvas_ir(canvas)
    nodes = list(_walk_nodes(builder.build()["root"]["children"]))
    honor_subscenes = [node for node in nodes if node.get("type") == "RasterSubscene"]

    assert len(honor_subscenes) == 3
    assert [node["natural_size"] for node in honor_subscenes] == [[380, 80], [180, 80], [180, 80]]
    assert [node["dst_size"] for node in honor_subscenes] == [[228.0, 48.0], [108.0, 48.0], [108.0, 48.0]]
    assert all(node["sampling"] == "catmull_rom" for node in honor_subscenes)
    assert all(node.get("shadow") for node in honor_subscenes)
    assert all(
        not image["path"].startswith("mem:")
        for subtree in honor_subscenes
        for image in _walk_nodes(subtree["children"])
        if image.get("type") == "Image"
    )
    assert not any(key.startswith("canvas_subtree.") for key in mem_images)


def test_profile_honor_module_skips_one_broken_subtree_without_reordering(monkeypatch) -> None:
    honors = [
        HonorRequest(honor_level=1),
        HonorRequest(honor_level=2),
        HonorRequest(honor_level=3),
    ]

    async def fake_build(request: HonorRequest):
        if request.honor_level == 2:
            raise FileNotFoundError("broken middle honor")
        return Canvas(380 if request.honor_level == 1 else 180, 80)

    monkeypatch.setattr(profile_drawer, "build_honor_badge_canvas_from_request", fake_build)
    module = asyncio.run(profile_drawer._build_profile_honor_module(type("Context", (), {"honors": honors})()))

    assert len(module.items) == 2
    assert all(isinstance(item, CanvasImageBox) for item in module.items)
    assert [item.natural_size for item in module.items] == [(380, 80), (180, 80)]
    assert all(item.require_asset_backed for item in module.items)


def test_profile_honor_module_skips_one_broken_cache_key(monkeypatch) -> None:
    honors = [
        HonorRequest(honor_level=1),
        HonorRequest(honor_level=2),
        HonorRequest(honor_level=3),
    ]

    async def fake_build(request: HonorRequest):
        return Canvas(380 if request.honor_level == 1 else 180, 80)

    def fake_cache_key(request: HonorRequest):
        if request.honor_level == 2:
            raise OSError("cannot stat middle honor")
        return f"honor-{request.honor_level}"

    monkeypatch.setattr(profile_drawer, "build_honor_badge_canvas_from_request", fake_build)
    monkeypatch.setattr(profile_drawer, "build_full_honor_cache_key", fake_cache_key)
    module = asyncio.run(profile_drawer._build_profile_honor_module(type("Context", (), {"honors": honors})()))

    assert [item.cache_key for item in module.items] == ["honor-1", "honor-3"]


def test_profile_honor_module_lowers_asset_backed_subtrees_without_external_fixture(tmp_path, monkeypatch) -> None:
    asset_path = tmp_path / "badge.png"
    Image.new("RGBA", (4, 3), (220, 40, 20, 255)).save(asset_path)
    asset_ref = asyncio.run(get_asset_image_ref(tmp_path, "badge.png", on_missing="raise"))
    honors = [HonorRequest(honor_level=1), HonorRequest(honor_level=2), HonorRequest(honor_level=3)]

    async def fake_build(request: HonorRequest):
        width = 380 if request.honor_level == 1 else 180
        child = Canvas(width, 80)
        child.add_item(ImageBox(asset_ref, image_size_mode="fill", size=(width, 80)))
        return child

    monkeypatch.setattr(profile_drawer, "build_honor_badge_canvas_from_request", fake_build)
    monkeypatch.setattr(profile_drawer, "build_full_honor_cache_key", lambda _request: None)
    module = asyncio.run(profile_drawer._build_profile_honor_module(type("Context", (), {"honors": honors})()))
    parent = Canvas()
    parent.add_item(module)

    builder, mem_images = build_canvas_ir(parent, assets_base_dir=str(tmp_path))
    nodes = list(_walk_nodes(builder.build()["root"]["children"]))
    honor_subscenes = [node for node in nodes if node.get("type") == "RasterSubscene"]

    assert mem_images == {}
    assert [node["natural_size"] for node in honor_subscenes] == [[380, 80], [180, 80], [180, 80]]
    assert [node["pos"] for node in honor_subscenes] == [[16.0, 0.0], [252.0, 0.0], [368.0, 0.0]]
    assert [node["dst_size"] for node in honor_subscenes] == [[228.0, 48.0], [108.0, 48.0], [108.0, 48.0]]
    assert all(node["sampling"] == "catmull_rom" for node in honor_subscenes)
    assert all(node["shadow"]["alpha"] == pytest.approx(0.6) for node in honor_subscenes)
    assert all(node["shadow"]["sigma"] == pytest.approx(3.0) for node in honor_subscenes)
    assert all(node["shadow"]["offset"] == [0.0, 0.0] for node in honor_subscenes)


def test_profile_honor_module_skips_canvas_that_fails_during_pillow_render(monkeypatch) -> None:
    class BrokenCanvas(Canvas):
        def get_img_sync(self, scale=None):
            raise RuntimeError("late child render failure")

    async def fake_build(_request: HonorRequest):
        return BrokenCanvas(380, 80)

    monkeypatch.setattr(profile_drawer, "build_honor_badge_canvas_from_request", fake_build)
    monkeypatch.setattr(profile_drawer, "build_full_honor_cache_key", lambda _request: None)
    context = type("Context", (), {"honors": [HonorRequest(honor_level=1)]})()
    module = asyncio.run(profile_drawer._build_profile_honor_module(context))
    parent = Canvas()
    parent.add_item(module)

    image = parent.get_img_sync()

    assert image.size == parent._get_self_size()
    assert image.getbbox() is None
    assert module.items[0].skip_on_error is True


def test_canvas_image_box_fill_with_both_dimensions_matches_image_box_contract() -> None:
    child = Canvas(4, 3)

    assert CanvasImageBox(child, image_size_mode="fill", size=(8, 8))._get_content_size() == (8, 8)
