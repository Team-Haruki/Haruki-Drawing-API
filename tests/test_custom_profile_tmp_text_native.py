from __future__ import annotations

import asyncio
from io import BytesIO
import json
import math
from pathlib import Path

from PIL import Image, ImageChops
import pytest

try:
    import haruki_skia_renderer as _native
except ImportError:  # pragma: no cover - native CI job exercises this file
    _native = None

from src.core.pillow_telemetry import (
    PILLOW_TOUCH_CUSTOM_PROFILE_MEM_RASTER,
    begin_pillow_touch_scope,
    end_pillow_touch_scope,
    take_pillow_touch_snapshot,
)
from src.sekai.profile.custom_profile.drawer import compose_custom_profile_card_image
import src.sekai.profile.custom_profile.renderer as renderer_mod
from src.sekai.profile.custom_profile.renderer import PNGRenderer
import src.sekai.profile.custom_profile.skia as skia_mod
from src.sekai.profile.model import CustomProfileCardRenderRequest
from src.sekai.skia_renderer.canvas import REQUIRED_NATIVE_IR_CAPABILITY
from src.sekai.skia_renderer.render_stats import get_render_stats, reset_render_stats

REPO_ROOT = Path(__file__).resolve().parent.parent
PAYLOAD_FILE = REPO_ROOT / "out" / "parity-payloads" / "custom_profile_card.json"
FONT_METADATA = REPO_ROOT / "data/custom_profile/tmp-font-assets/cn/metadata.json"


@pytest.fixture(autouse=True)
def _clean_render_stats():
    reset_render_stats()
    yield
    reset_render_stats()


def _text_only_request(
    *,
    rotation_degrees: float = 0.0,
    scale: tuple[float, float] = (1.0, 1.0),
    outline_size: float = 0.0,
) -> CustomProfileCardRenderRequest:
    raw = json.loads(PAYLOAD_FILE.read_text(encoding="utf-8"))
    layout = raw["card"]["customProfileCard"]
    for key, value in layout.items():
        if isinstance(value, list) and key != "texts":
            value.clear()
    layout["texts"] = layout["texts"][:1]
    item = layout["texts"][0]
    half_angle = math.radians(rotation_degrees) / 2.0
    item["objectData"]["rotation"] = {
        "x": 0.0,
        "y": 0.0,
        "z": math.sin(half_angle),
        "w": math.cos(half_angle),
    }
    item["objectData"]["scale"] = {"x": scale[0], "y": scale[1], "z": 1.0}
    item["outlineSize"] = outline_size
    return CustomProfileCardRenderRequest.model_validate(raw)


def _walk_nodes(node: dict):
    yield node
    for child in node.get("children", ()):
        yield from _walk_nodes(child)


def _rgb_diff_metrics(reference: Image.Image, rendered: Image.Image) -> tuple[float, int]:
    histogram = ImageChops.difference(reference, rendered).convert("RGB").histogram()
    channel_pixels = reference.width * reference.height * 3
    mean = sum(value * histogram[channel * 256 + value] for channel in range(3) for value in range(256))
    mean /= channel_pixels
    threshold = channel_pixels * 0.99
    cumulative = 0
    for value in range(256):
        cumulative += sum(histogram[channel * 256 + value] for channel in range(3))
        if cumulative >= threshold:
            return mean, value
    return mean, 255


class _NativeProxy:
    ASSET_INFO_CAPABILITY = getattr(_native, "ASSET_INFO_CAPABILITY", 0)
    TEXT_METRICS_CAPABILITY = getattr(_native, "TEXT_METRICS_CAPABILITY", 0)

    def __init__(self) -> None:
        self.ir_json: bytes | None = None
        self.mem_images: dict[str, tuple] | None = None

    def asset_image_info(self, *args):
        return _native.asset_image_info(*args)

    def measure_text_batch(self, *args):
        return _native.measure_text_batch(*args)

    def render_scene(self, ir_json, mem_images):
        self.ir_json = bytes(ir_json)
        self.mem_images = dict(mem_images)
        return _native.render_scene(ir_json, mem_images)


def _unexpected_raster(*args, **kwargs):  # pragma: no cover - only called on regression
    raise AssertionError("strict simple TMP text must not enter a Pillow raster helper")


@pytest.mark.skipif(
    _native is None
    or getattr(_native, "IR_CAPABILITY", 0) < REQUIRED_NATIVE_IR_CAPABILITY
    or getattr(_native, "TEXT_METRICS_CAPABILITY", 0) < 1,
    reason="current native custom-profile renderer is required",
)
@pytest.mark.skipif(
    not PAYLOAD_FILE.is_file() or not FONT_METADATA.is_file(),
    reason="custom-profile parity fixture and extracted TMP metadata are required",
)
@pytest.mark.parametrize(
    ("rotation_degrees", "scale", "mean_budget", "p99_budget"),
    [
        pytest.param(0.0, (1.0, 1.0), 0.25, 2, id="axis-aligned"),
        pytest.param(17.0, (1.25, 0.72), 0.25, 2, id="rotated-nonuniform"),
    ],
)
def test_real_plain_tmp_text_is_native_pixel_pure_and_matches_pillow(
    monkeypatch,
    rotation_degrees,
    scale,
    mean_budget,
    p99_budget,
):
    from src.settings import settings

    request = _text_only_request(rotation_degrees=rotation_degrees, scale=scale)
    pillow = asyncio.run(compose_custom_profile_card_image(request)).convert("RGBA")
    proxy = _NativeProxy()

    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)
    monkeypatch.setattr(skia_mod, "load_native_renderer", lambda: proxy)
    monkeypatch.setattr(PNGRenderer, "render_content_for_card", _unexpected_raster)
    monkeypatch.setattr(PNGRenderer, "prepare_direct_sdf_quads", _unexpected_raster)
    monkeypatch.setattr(PNGRenderer, "render_content_direct_on_card", _unexpected_raster)
    monkeypatch.setattr(PNGRenderer, "render_text", _unexpected_raster)
    monkeypatch.setattr(PNGRenderer, "render_tmp_text_box", _unexpected_raster)
    monkeypatch.setattr(renderer_mod, "load_font", _unexpected_raster)

    token = begin_pillow_touch_scope()
    try:
        payload = asyncio.run(skia_mod.try_render_custom_profile_card_payload(request))
        pillow_touches = take_pillow_touch_snapshot()
    finally:
        end_pillow_touch_scope(token)

    assert payload is not None
    stats = get_render_stats()["endpoints"][skia_mod.CUSTOM_PROFILE_ENDPOINT]
    assert stats["native_pure"] == 1
    assert stats["native_hybrid"] == 0
    assert stats["pillow_touch_reasons"] == {}
    assert pillow_touches.counts == {}
    assert payload.native_metrics["custom_profile_visible_elements"] == 1
    assert payload.native_metrics["custom_profile_native_elements"] == 1
    assert payload.native_metrics["custom_profile_hybrid_elements"] == 0
    assert payload.native_metrics["custom_profile_mem_images"] == 0
    assert payload.native_metrics["custom_profile_mem_bytes"] == 0
    assert proxy.mem_images == {}
    assert proxy.ir_json is not None
    assert b"mem:" not in proxy.ir_json

    scene = json.loads(proxy.ir_json)
    nodes = list(_walk_nodes(scene["root"]))
    text_nodes = [node for node in nodes if node["type"] == "Text"]
    assert len(text_nodes) == 3
    tmp_font_names = {node["font"]["name"] for node in text_nodes}
    assert len(tmp_font_names) == 1
    tmp_font_name = tmp_font_names.pop()
    assert tmp_font_name.startswith("custom_profile_tmp_")
    tmp_font_path = Path(scene["fonts"]["extra"][tmp_font_name])
    assert tmp_font_path.is_file()
    assert tmp_font_path.name.startswith("FOT-RodinNTLGPro-DB")
    assert all(node["baseline"] == "alphabetic" for node in text_nodes)

    native = Image.open(BytesIO(payload.image_bytes)).convert("RGBA")
    mean, p99 = _rgb_diff_metrics(pillow, native)
    assert mean <= mean_budget, mean
    assert p99 <= p99_budget, p99
    assert ImageChops.difference(pillow.getchannel("A"), native.getchannel("A")).getbbox() is None


@pytest.mark.skipif(
    _native is None
    or getattr(_native, "IR_CAPABILITY", 0) < REQUIRED_NATIVE_IR_CAPABILITY
    or getattr(_native, "TEXT_METRICS_CAPABILITY", 0) < 1,
    reason="current native custom-profile renderer is required",
)
@pytest.mark.skipif(
    not PAYLOAD_FILE.is_file() or not FONT_METADATA.is_file(),
    reason="custom-profile parity fixture and extracted TMP metadata are required",
)
def test_outline_tmp_text_is_rejected_by_native_subset_and_falls_back_atomically(monkeypatch):
    from src.settings import settings

    request = _text_only_request(outline_size=0.2)
    pillow = asyncio.run(compose_custom_profile_card_image(request)).convert("RGBA")
    proxy = _NativeProxy()
    original_render = PNGRenderer.render_content_for_card
    fallback_calls = 0

    def _render_fallback(renderer, content):
        nonlocal fallback_calls
        fallback_calls += 1
        return original_render(renderer, content)

    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)
    monkeypatch.setattr(skia_mod, "load_native_renderer", lambda: proxy)
    monkeypatch.setattr(PNGRenderer, "render_content_for_card", _render_fallback)
    monkeypatch.setattr(PNGRenderer, "prepare_direct_sdf_quads", _unexpected_raster)
    monkeypatch.setattr(PNGRenderer, "render_content_direct_on_card", _unexpected_raster)

    token = begin_pillow_touch_scope()
    try:
        payload = asyncio.run(skia_mod.try_render_custom_profile_card_payload(request))
    finally:
        end_pillow_touch_scope(token)

    assert payload is not None
    assert fallback_calls == 1
    stats = get_render_stats()["endpoints"][skia_mod.CUSTOM_PROFILE_ENDPOINT]
    assert stats["native_pure"] == 0
    assert stats["native_hybrid"] == 1
    assert stats["pillow_touch_reasons"] == {PILLOW_TOUCH_CUSTOM_PROFILE_MEM_RASTER: {"renders": 1, "touches": 1}}
    assert payload.native_metrics["custom_profile_visible_elements"] == 1
    assert payload.native_metrics["custom_profile_native_elements"] == 0
    assert payload.native_metrics["custom_profile_hybrid_elements"] == 1
    assert payload.native_metrics["custom_profile_mem_images"] == 1
    assert payload.native_metrics["custom_profile_mem_bytes"] > 0
    assert proxy.mem_images is not None
    assert len(proxy.mem_images) == 1
    assert proxy.ir_json is not None
    assert b"mem:" in proxy.ir_json

    scene = json.loads(proxy.ir_json)
    assert not any(node["type"] == "Text" for node in _walk_nodes(scene["root"]))
    native = Image.open(BytesIO(payload.image_bytes)).convert("RGBA")
    mean, p99 = _rgb_diff_metrics(pillow, native)
    assert mean == 0.0
    assert p99 == 0
    assert ImageChops.difference(pillow.getchannel("A"), native.getchannel("A")).getbbox() is None
