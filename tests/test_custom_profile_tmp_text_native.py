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
    text: str = "Skia\n\n中文・日本語",
    font_name: str = "FOT-RodinNTLGPro-DB",
) -> CustomProfileCardRenderRequest:
    half_angle = math.radians(rotation_degrees) / 2.0
    item = {
        "fontId": 1,
        "colorId": 1,
        "outlineColorId": 2,
        "outlineAlpha": 1.0,
        "outlineSize": outline_size,
        "alpha": 1.0,
        "size": 28.0,
        "text": text,
        "objectData": {
            "position": {"x": -180.0, "y": 96.0, "z": 0.0},
            "scale": {"x": scale[0], "y": scale[1], "z": 1.0},
            "rotation": {
                "x": 0.0,
                "y": 0.0,
                "z": math.sin(half_angle),
                "w": math.cos(half_angle),
            },
            "layer": 1,
            "visible": True,
        },
    }
    return CustomProfileCardRenderRequest.model_validate(
        {
            "kind": "pjsk_custom_profile_card",
            "region": "cn",
            "card": {
                "seq": 1,
                "customProfileCardId": 1,
                "customProfileCard": {"texts": [item]},
            },
            "resources": {
                "customProfileTextFonts": [
                    {"id": 1, "fontName": font_name},
                ],
                "customProfileTextColors": [
                    {"id": 1, "colorCode": "#33aaee"},
                    {"id": 2, "colorCode": "#ffdd44"},
                ],
            },
            "profile_context": {},
        }
    )


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
@pytest.mark.skipif(not FONT_METADATA.is_file(), reason="extracted TMP metadata is required")
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
    assert [node["text"] for node in text_nodes] == ["Skia", "中文・日本語"]
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
@pytest.mark.skipif(not FONT_METADATA.is_file(), reason="extracted TMP metadata is required")
@pytest.mark.parametrize(
    ("font_name", "text", "expected_node_type", "expected_font_fragment", "expected_codepoint"),
    [
        pytest.param(
            "FOT-RodinNTLGPro-DB",
            "<rotate=12>●あ</rotate>",
            "SdfFontQuad",
            "FOT-RodinNTLGPro-DB",
            None,
            id="dynamic-source-symbol-cjk",
        ),
        pytest.param(
            "FOT-RodinNTLGPro-DB-OnDemand",
            "<color=#ff66bb><rotate=-9>●一</rotate></color>",
            "SdfAtlasQuad",
            None,
            None,
            id="static-atlas-rich-symbol-cjk",
        ),
        pytest.param(
            "FOT-PopHappinessStd-EB",
            "<rotate=7>乗</rotate>",
            "SdfFontQuad",
            "FOT-RodinNTLGPro-DB",
            None,
            id="fallback-source-cjk",
        ),
        pytest.param(
            "FOT-RodinNTLGPro-DB",
            "<rotate=-5>😀</rotate>",
            "SdfFontQuad",
            "FOT-RodinNTLGPro-DB",
            ord("□"),
            id="missing-emoji-replacement",
        ),
    ],
)
def test_sparse_tmp_text_categories_use_native_glyphs_without_mem_transport(
    monkeypatch,
    font_name,
    text,
    expected_node_type,
    expected_font_fragment,
    expected_codepoint,
):
    from src.settings import settings

    request = _text_only_request(
        outline_size=0.2,
        text=text,
        font_name=font_name,
    )
    pillow = asyncio.run(compose_custom_profile_card_image(request)).convert("RGBA")
    proxy = _NativeProxy()

    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)
    monkeypatch.setattr(skia_mod, "load_native_renderer", lambda: proxy)
    monkeypatch.setattr(PNGRenderer, "render_content_for_card", _unexpected_raster)
    monkeypatch.setattr(PNGRenderer, "render_content_direct_on_card", _unexpected_raster)

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
    assert not any(node["type"] == "Text" for node in _walk_nodes(scene["root"]))
    sparse_nodes = [node for node in _walk_nodes(scene["root"]) if node["type"] == expected_node_type]
    assert sparse_nodes
    assert all(node["shading"]["underlay"] is not None for node in sparse_nodes)
    if expected_font_fragment is not None:
        registered_fonts = scene["fonts"].get("extra", {})
        used_font_names = {node["font"]["name"] for node in sparse_nodes}
        assert all(expected_font_fragment in registered_fonts[name] for name in used_font_names)
    if expected_codepoint is not None:
        assert {node["codepoint"] for node in sparse_nodes} == {expected_codepoint}
    native = Image.open(BytesIO(payload.image_bytes)).convert("RGBA")
    mean, p99 = _rgb_diff_metrics(pillow, native)
    assert mean <= 0.7, mean
    assert p99 <= 2, p99
    assert ImageChops.difference(pillow.getchannel("A"), native.getchannel("A")).getbbox() is None
