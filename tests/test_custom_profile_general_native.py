from __future__ import annotations

import asyncio
from io import BytesIO
import json
from pathlib import Path

from PIL import Image, ImageChops
import pytest

try:
    import haruki_skia_renderer as _native
except ImportError:  # pragma: no cover - native CI job exercises this file
    _native = None

from src.core.pillow_telemetry import (
    begin_pillow_touch_scope,
    end_pillow_touch_scope,
)
from src.sekai.profile.custom_profile.drawer import compose_custom_profile_card_image
import src.sekai.profile.custom_profile.skia as skia_mod
from src.sekai.profile.model import CustomProfileCardRenderRequest
from src.sekai.skia_renderer.canvas import REQUIRED_NATIVE_IR_CAPABILITY
from src.sekai.skia_renderer.render_stats import get_render_stats, reset_render_stats

REPO_ROOT = Path(__file__).resolve().parent.parent
PAYLOAD_FILE = REPO_ROOT / "out" / "parity-payloads" / "custom_profile_card.json"
COLLECTIONS_PAYLOAD_FILE = REPO_ROOT / "out" / "parity-payloads" / "custom_profile_card_collections.json"
SHARED_GENERAL_IDS = {2, 4, 13}
STATS_GENERAL_IDS = {9, 10, 12}


@pytest.fixture(autouse=True)
def _clean_render_stats():
    reset_render_stats()
    yield
    reset_render_stats()


def _shared_general_request() -> CustomProfileCardRenderRequest:
    raw = json.loads(PAYLOAD_FILE.read_text(encoding="utf-8"))
    layout = raw["card"]["customProfileCard"]
    for key, value in layout.items():
        if isinstance(value, list) and key != "generals":
            value.clear()
    layout["generals"] = [
        item for item in layout["generals"] if int(item.get("type", item.get("id", 0)) or 0) in SHARED_GENERAL_IDS
    ]
    return CustomProfileCardRenderRequest.model_validate(raw)


def _stats_general_request() -> CustomProfileCardRenderRequest:
    raw = json.loads(COLLECTIONS_PAYLOAD_FILE.read_text(encoding="utf-8"))
    layout = raw["card"]["customProfileCard"]
    for key, value in layout.items():
        if isinstance(value, list) and key != "generals":
            value.clear()
    layout["generals"] = [
        item for item in layout["generals"] if int(item.get("type", item.get("id", 0)) or 0) in STATS_GENERAL_IDS
    ]
    return CustomProfileCardRenderRequest.model_validate(raw)


def _collections_request() -> CustomProfileCardRenderRequest:
    return CustomProfileCardRenderRequest.model_validate_json(COLLECTIONS_PAYLOAD_FILE.read_text(encoding="utf-8"))


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


def test_old_native_wheel_declines_general_text_metrics_without_pillow_compat(monkeypatch, tmp_path):
    font = tmp_path / "font.ttf"
    font.touch()

    class _OldNative:
        TEXT_METRICS_CAPABILITY = 0

        def measure_text_batch(self, *args):  # pragma: no cover - must not run
            raise AssertionError("an old wheel must not be queried")

    monkeypatch.setattr(skia_mod, "load_native_renderer", lambda: _OldNative())
    assert skia_mod._NativeGeneralTextMetrics.create(font) is None


def test_claimed_native_text_metrics_reject_malformed_results(tmp_path):
    font = tmp_path / "font.ttf"
    font.touch()
    metrics = skia_mod._NativeGeneralTextMetrics(
        lambda *args: [{"pillow_bbox": (0, 0, float("nan"), 10), "ascent": 8, "descent": 2}],
        font,
    )

    with pytest.raises(ValueError, match="invalid Pillow-relative bbox"):
        metrics.text_bbox("Haruki", skia_mod.GeneralFontRef(), 24)


@pytest.mark.skipif(
    _native is None
    or getattr(_native, "IR_CAPABILITY", 0) < REQUIRED_NATIVE_IR_CAPABILITY
    or getattr(_native, "TEXT_METRICS_CAPABILITY", 0) < 1,
    reason="SlicedImage + native text metrics renderer is required",
)
@pytest.mark.skipif(not PAYLOAD_FILE.is_file(), reason="custom profile parity fixture not present")
def test_shared_general_prefabs_are_native_pixel_pure_and_match_pillow(monkeypatch):
    from src.settings import settings

    request = _shared_general_request()
    pillow = asyncio.run(compose_custom_profile_card_image(request)).convert("RGBA")
    captured: dict[str, object] = {}

    class _NativeProxy:
        ASSET_INFO_CAPABILITY = getattr(_native, "ASSET_INFO_CAPABILITY", 0)
        TEXT_METRICS_CAPABILITY = getattr(_native, "TEXT_METRICS_CAPABILITY", 0)

        def asset_image_info(self, *args):
            return _native.asset_image_info(*args)

        def measure_text_batch(self, *args):
            return _native.measure_text_batch(*args)

        def render_scene(self, ir_json, mem_images):
            captured["ir_json"] = bytes(ir_json)
            captured["mem_images"] = dict(mem_images)
            return _native.render_scene(ir_json, mem_images)

    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)
    monkeypatch.setattr(skia_mod, "load_native_renderer", lambda: _NativeProxy())

    token = begin_pillow_touch_scope()
    try:
        payload = asyncio.run(skia_mod.try_render_custom_profile_card_payload(request))
    finally:
        end_pillow_touch_scope(token)

    assert payload is not None
    stats = get_render_stats()["endpoints"][skia_mod.CUSTOM_PROFILE_ENDPOINT]
    assert stats["native_pure"] == 1
    assert stats["native_hybrid"] == 0
    assert stats["pillow_touch_reasons"] == {}
    assert payload.native_metrics["custom_profile_native_elements"] == 3
    assert payload.native_metrics["custom_profile_hybrid_elements"] == 0
    assert payload.native_metrics["custom_profile_mem_images"] == 0
    assert payload.native_metrics["custom_profile_mem_bytes"] == 0
    assert captured["mem_images"] == {}
    assert b"mem:" not in captured["ir_json"]

    scene = json.loads(captured["ir_json"])
    general_font = Path(scene["fonts"]["extra"]["custom_profile_general"])
    assert general_font.is_file()
    assert general_font.name.startswith("FOT-RodinNTLGPro-DB")
    nodes = list(_walk_nodes(scene["root"]))
    assert sum(node["type"] == "UnitySubscene" for node in nodes) == 3
    assert any(node["type"] == "SlicedImage" for node in nodes)
    text_nodes = [node for node in nodes if node["type"] == "Text"]
    assert text_nodes
    assert all(node["font"]["name"] == "custom_profile_general" for node in text_nodes)

    native = Image.open(BytesIO(payload.image_bytes)).convert("RGBA")
    diff = ImageChops.difference(pillow, native)
    mean, p99 = _rgb_diff_metrics(pillow, native)
    assert mean <= 2.0, mean
    assert p99 <= 25, p99
    assert diff.getchannel("A").getbbox() is None


@pytest.mark.skipif(
    _native is None
    or getattr(_native, "IR_CAPABILITY", 0) < REQUIRED_NATIVE_IR_CAPABILITY
    or getattr(_native, "TEXT_METRICS_CAPABILITY", 0) < 1,
    reason="SlicedImage + native text metrics renderer is required",
)
@pytest.mark.skipif(not COLLECTIONS_PAYLOAD_FILE.is_file(), reason="custom profile collections fixture not present")
def test_stats_general_prefabs_are_native_pixel_pure_and_match_pillow(monkeypatch):
    from src.settings import settings

    request = _stats_general_request()
    pillow = asyncio.run(compose_custom_profile_card_image(request)).convert("RGBA")
    captured: dict[str, object] = {}

    class _NativeProxy:
        ASSET_INFO_CAPABILITY = getattr(_native, "ASSET_INFO_CAPABILITY", 0)
        TEXT_METRICS_CAPABILITY = getattr(_native, "TEXT_METRICS_CAPABILITY", 0)

        def asset_image_info(self, *args):
            return _native.asset_image_info(*args)

        def measure_text_batch(self, *args):
            return _native.measure_text_batch(*args)

        def render_scene(self, ir_json, mem_images):
            captured["ir_json"] = bytes(ir_json)
            captured["mem_images"] = dict(mem_images)
            return _native.render_scene(ir_json, mem_images)

    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)
    monkeypatch.setattr(skia_mod, "load_native_renderer", lambda: _NativeProxy())

    token = begin_pillow_touch_scope()
    try:
        payload = asyncio.run(skia_mod.try_render_custom_profile_card_payload(request))
    finally:
        end_pillow_touch_scope(token)

    assert payload is not None
    stats = get_render_stats()["endpoints"][skia_mod.CUSTOM_PROFILE_ENDPOINT]
    assert stats["native_pure"] == 1
    assert stats["native_hybrid"] == 0
    assert stats["pillow_touch_reasons"] == {}
    assert payload.native_metrics["custom_profile_native_elements"] == 3
    assert payload.native_metrics["custom_profile_hybrid_elements"] == 0
    assert payload.native_metrics["custom_profile_mem_images"] == 0
    assert payload.native_metrics["custom_profile_mem_bytes"] == 0
    assert captured["mem_images"] == {}
    assert b"mem:" not in captured["ir_json"]

    scene = json.loads(captured["ir_json"])
    nodes = list(_walk_nodes(scene["root"]))
    assert sum(node["type"] == "UnitySubscene" for node in nodes) == 3
    assert any(node["type"] == "RoundRect" for node in nodes)
    assert any(node["type"] == "SlicedImage" for node in nodes)
    assert all(node["font"]["name"] == "custom_profile_general" for node in nodes if node["type"] == "Text")

    native = Image.open(BytesIO(payload.image_bytes)).convert("RGBA")
    diff = ImageChops.difference(pillow, native)
    mean, p99 = _rgb_diff_metrics(pillow, native)
    assert mean <= 2.0, mean
    assert p99 <= 25, p99
    assert diff.getchannel("A").getbbox() is None


@pytest.mark.skipif(
    _native is None
    or getattr(_native, "IR_CAPABILITY", 0) < REQUIRED_NATIVE_IR_CAPABILITY
    or getattr(_native, "TEXT_METRICS_CAPABILITY", 0) < 1,
    reason="current native custom-profile renderer is required",
)
@pytest.mark.skipif(not COLLECTIONS_PAYLOAD_FILE.is_file(), reason="custom profile collections fixture not present")
def test_collections_fixture_is_fully_native_without_pillow_pixels(monkeypatch):
    from src.settings import settings

    request = _collections_request()
    pillow = asyncio.run(compose_custom_profile_card_image(request)).convert("RGBA")
    captured: dict[str, object] = {}

    class _NativeProxy:
        ASSET_INFO_CAPABILITY = getattr(_native, "ASSET_INFO_CAPABILITY", 0)
        TEXT_METRICS_CAPABILITY = getattr(_native, "TEXT_METRICS_CAPABILITY", 0)

        def asset_image_info(self, *args):
            return _native.asset_image_info(*args)

        def measure_text_batch(self, *args):
            return _native.measure_text_batch(*args)

        def render_scene(self, ir_json, mem_images):
            captured["ir_json"] = bytes(ir_json)
            captured["mem_images"] = dict(mem_images)
            return _native.render_scene(ir_json, mem_images)

    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)
    monkeypatch.setattr(skia_mod, "load_native_renderer", lambda: _NativeProxy())

    token = begin_pillow_touch_scope()
    try:
        payload = asyncio.run(skia_mod.try_render_custom_profile_card_payload(request))
    finally:
        end_pillow_touch_scope(token)

    assert payload is not None
    stats = get_render_stats()["endpoints"][skia_mod.CUSTOM_PROFILE_ENDPOINT]
    assert stats["native_pure"] == 1
    assert stats["native_hybrid"] == 0
    assert stats["pillow_touch_reasons"] == {}
    assert payload.native_metrics["custom_profile_visible_elements"] == 9
    assert payload.native_metrics["custom_profile_native_elements"] == 9
    assert payload.native_metrics["custom_profile_hybrid_elements"] == 0
    assert payload.native_metrics["custom_profile_mem_images"] == 0
    assert payload.native_metrics["custom_profile_mem_bytes"] == 0
    assert captured["mem_images"] == {}
    assert b"mem:" not in captured["ir_json"]

    native = Image.open(BytesIO(payload.image_bytes)).convert("RGBA")
    diff = ImageChops.difference(pillow, native)
    mean, p99 = _rgb_diff_metrics(pillow, native)
    assert mean <= 2.0, mean
    assert p99 <= 41, p99
    assert diff.getchannel("A").getbbox() is None
