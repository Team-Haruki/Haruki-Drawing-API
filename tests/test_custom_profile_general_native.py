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
from src.sekai.profile.custom_profile.general_prefab import (
    GeneralAssetImageOp,
    GeneralPrefabDisplayList,
    GeneralRoundedRectOp,
    GeneralSpriteChoiceOp,
    GeneralSpriteOp,
    GeneralTextOp,
    GeneralViewportOp,
)
from src.sekai.profile.custom_profile.renderer import GENERAL_NATIVE_SIZES
import src.sekai.profile.custom_profile.skia as skia_mod
from src.sekai.profile.model import CustomProfileCardRenderRequest
from src.sekai.skia_renderer.canvas import REQUIRED_NATIVE_IR_CAPABILITY
from src.sekai.skia_renderer.render_stats import get_render_stats, reset_render_stats

REPO_ROOT = Path(__file__).resolve().parent.parent
PAYLOAD_FILE = REPO_ROOT / "out" / "parity-payloads" / "custom_profile_card.json"
COLLECTIONS_PAYLOAD_FILE = REPO_ROOT / "out" / "parity-payloads" / "custom_profile_card_collections.json"
SHARED_GENERAL_IDS = {2, 4, 13}
STATS_GENERAL_IDS = {9, 10, 12}
CARD_GENERAL_IDS = {3, 5}
HONOR_DECK_GENERAL_IDS = {6}


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


def _x_general_request() -> CustomProfileCardRenderRequest:
    raw = json.loads(PAYLOAD_FILE.read_text(encoding="utf-8"))
    layout = raw["card"]["customProfileCard"]
    for value in layout.values():
        if isinstance(value, list):
            value.clear()
    resource_id = 900_001
    layout["generals"] = [
        {
            "type": resource_id,
            "objectData": {
                "visible": True,
                "layer": 1,
                "position": {"x": 0, "y": 0},
                "scale": {"x": 1, "y": 1},
                "rotation": {"z": 0, "w": 1},
            },
        }
    ]
    raw["resources"]["customProfilePlayerInfoResources"] = {str(resource_id): {"id": resource_id, "fileName": "X"}}
    raw["profile_context"]["userProfile"] = {"twitterId": "category_fixture"}
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


def _card_general_request() -> CustomProfileCardRenderRequest:
    raw = json.loads(PAYLOAD_FILE.read_text(encoding="utf-8"))
    layout = raw["card"]["customProfileCard"]
    for key, value in layout.items():
        if isinstance(value, list) and key != "generals":
            value.clear()
    layout["generals"] = [
        item for item in layout["generals"] if int(item.get("type", item.get("id", 0)) or 0) in CARD_GENERAL_IDS
    ]
    return CustomProfileCardRenderRequest.model_validate(raw)


def _honor_deck_general_request() -> CustomProfileCardRenderRequest:
    raw = json.loads(PAYLOAD_FILE.read_text(encoding="utf-8"))
    layout = raw["card"]["customProfileCard"]
    for key, value in layout.items():
        if isinstance(value, list) and key != "generals":
            value.clear()
    layout["generals"] = [
        item for item in layout["generals"] if int(item.get("type", item.get("id", 0)) or 0) in HONOR_DECK_GENERAL_IDS
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


def test_every_compat_general_prefab_is_registered_on_a_native_path() -> None:
    shared = set(skia_mod._NATIVE_GENERAL_PREFABS)
    card = set(skia_mod._NATIVE_CARD_GENERAL_PREFABS)
    honor_deck = {"HonorDeck"}

    assert shared.isdisjoint(card)
    assert shared.isdisjoint(honor_deck)
    assert card.isdisjoint(honor_deck)
    assert shared | card | honor_deck == set(GENERAL_NATIVE_SIZES)


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


def test_native_general_preflight_and_emission_cover_all_shared_ops(tmp_path, monkeypatch):
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    sprite_path = asset_root / "sprite.png"
    choice_path = asset_root / "choice.png"
    image_path = asset_root / "image.png"
    for path in (sprite_path, choice_path, image_path):
        Image.new("RGBA", (4, 4), (1, 2, 3, 255)).save(path)
    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", asset_root)

    class _Renderer:
        @staticmethod
        def unity_ui_sprite_path(name):
            return {"sprite": sprite_path, "choice": choice_path}.get(name)

    class _Metrics:
        @staticmethod
        def text_placement(op):
            return "left", float(op.pos[1]) + 1.0

    fallback_rect = GeneralRoundedRectOp((5, 0, 9, 4), 1.0, (20, 30, 40, 128))
    fallback_text = GeneralTextOp("fallback", (14, 3), 4, (255, 255, 255, 255))
    nested_text = GeneralTextOp("nested", (1, 2), 4, (255, 255, 255, 255))
    display_list = GeneralPrefabDisplayList(
        "test",
        (32, 16),
        (
            GeneralSpriteOp("sprite", (0, 0, 4, 4), sliced_border=(1, 1, 1, 1), tint=(1, 1, 1, 1)),
            GeneralSpriteOp("missing", (5, 0, 9, 4), resource_policy="fallback", fallback=fallback_rect),
            GeneralSpriteChoiceOp(("missing", "choice"), (10, 0, 14, 4), sampling="bicubic"),
            GeneralSpriteChoiceOp(("missing",), (14, 0, 18, 4), fallback_text=fallback_text),
            GeneralAssetImageOp("plain", image_path, (18, 0, 22, 4), sampling="bilinear"),
            GeneralAssetImageOp("clipped", image_path, (22, 0, 26, 4), clip_radius=1.0),
            GeneralAssetImageOp(
                "fallback",
                None,
                (26, 0, 30, 4),
                resource_policy="fallback",
                fallback=GeneralRoundedRectOp((26, 0, 30, 4), 1.0, (50, 60, 70, 255)),
            ),
            GeneralTextOp("direct", (1, 7), 4, (255, 255, 255, 255)),
            GeneralViewportOp((0, 8), (8, 4), (8, 8), (nested_text,)),
        ),
    )

    prepared = skia_mod._prepare_native_general_display_list(_Renderer(), _Metrics(), display_list)
    assert prepared is not None
    assert prepared.resource_paths[id(display_list.ops[0])] == "sprite.png"
    assert prepared.resource_paths[id(display_list.ops[2])] == "choice.png"
    assert prepared.resource_paths[id(display_list.ops[3])] is None
    assert prepared.resource_paths[id(display_list.ops[4])] == "image.png"
    assert prepared.text_placements[id(fallback_text)] == ("left", 4.0)
    assert prepared.text_placements[id(nested_text)] == ("left", 3.0)

    builder = skia_mod._new_builder(32, 16)
    scene = skia_mod._SceneAssembler(builder, (32, 16), 1024 * 1024)
    skia_mod._emit_native_general_ops(scene, prepared, display_list.ops)
    nodes = list(_walk_nodes(builder.build()["root"]))

    assert any(node["type"] == "SlicedImage" for node in nodes)
    assert sum(node["type"] == "Image" for node in nodes) == 3
    assert sum(node["type"] == "RoundRect" for node in nodes) == 2
    assert {node["text"] for node in nodes if node["type"] == "Text"} == {"fallback", "direct", "nested"}
    assert any(node.get("clip", {}).get("kind") == "pillow_rrect" for node in nodes)
    assert any(node.get("clip", {}).get("kind") == "rect" for node in nodes)


@pytest.mark.parametrize(
    "op_factory",
    [
        lambda root, outside: GeneralSpriteOp("missing", (0, 0, 4, 4), resource_policy="required"),
        lambda root, outside: GeneralSpriteOp("outside", (0, 0, 4, 4)),
        lambda root, outside: GeneralSpriteChoiceOp(("outside",), (0, 0, 4, 4)),
        lambda root, outside: GeneralAssetImageOp(
            "required",
            root / "missing.png",
            (0, 0, 4, 4),
            resource_policy="required",
        ),
        lambda root, outside: GeneralAssetImageOp("outside", outside, (0, 0, 4, 4)),
        lambda root, outside: GeneralAssetImageOp(
            "off-center-cover",
            root / "ready.png",
            (0, 0, 4, 4),
            fit="cover",
            align=(0.0, 0.5),
        ),
    ],
)
def test_native_general_preflight_declines_unsafe_resources(tmp_path, monkeypatch, op_factory):
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    ready_path = asset_root / "ready.png"
    outside_path = tmp_path / "outside.png"
    Image.new("RGBA", (2, 2)).save(ready_path)
    Image.new("RGBA", (2, 2)).save(outside_path)
    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", asset_root)

    class _Renderer:
        @staticmethod
        def unity_ui_sprite_path(name):
            return outside_path if name == "outside" else None

    class _Metrics:
        @staticmethod
        def text_placement(op):  # pragma: no cover - these cases contain no text
            raise AssertionError(op)

    display_list = GeneralPrefabDisplayList("unsafe", (8, 8), (op_factory(asset_root, outside_path),))
    assert skia_mod._prepare_native_general_display_list(_Renderer(), _Metrics(), display_list) is None


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
@pytest.mark.skipif(not PAYLOAD_FILE.is_file(), reason="custom profile parity fixture not present")
def test_x_general_category_is_native_pixel_pure_and_matches_shared_pillow(monkeypatch):
    from src.settings import settings

    request = _x_general_request()
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
    assert payload.native_metrics["custom_profile_native_elements"] == 1
    assert payload.native_metrics["custom_profile_hybrid_elements"] == 0
    assert captured["mem_images"] == {}
    scene = json.loads(captured["ir_json"])
    nodes = list(_walk_nodes(scene["root"]))
    assert any(node["type"] == "Text" and node["text"] == "X" for node in nodes)
    assert any(node["type"] == "Text" and node["text"] == "@category_fixture" for node in nodes)

    native = Image.open(BytesIO(payload.image_bytes)).convert("RGBA")
    diff = ImageChops.difference(pillow, native)
    mean, p99 = _rgb_diff_metrics(pillow, native)
    assert mean <= 1.0, mean
    assert p99 <= 20, p99
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
    reason="UnitySubscene + native text metrics renderer is required",
)
@pytest.mark.skipif(not PAYLOAD_FILE.is_file(), reason="custom profile parity fixture not present")
def test_card_general_prefabs_are_native_pixel_pure_and_match_pillow(monkeypatch):
    from src.settings import settings

    request = _card_general_request()
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
    assert payload.native_metrics["custom_profile_native_elements"] == 2
    assert payload.native_metrics["custom_profile_hybrid_elements"] == 0
    assert payload.native_metrics["custom_profile_mem_images"] == 0
    assert payload.native_metrics["custom_profile_mem_bytes"] == 0
    assert captured["mem_images"] == {}
    assert b"mem:" not in captured["ir_json"]

    scene = json.loads(captured["ir_json"])
    nodes = list(_walk_nodes(scene["root"]))
    # LeaderCard has one outer isolated surface; Deck has one outer surface plus five
    # compose-then-resize member surfaces.
    assert sum(node["type"] == "UnitySubscene" for node in nodes) == 7
    assert any(node["type"] == "Rect" and node.get("blend") == "src" for node in nodes)
    assert all(node["font"]["name"] == "custom_profile_general" for node in nodes if node["type"] == "Text")

    native = Image.open(BytesIO(payload.image_bytes)).convert("RGBA")
    diff = ImageChops.difference(pillow, native)
    mean, p99 = _rgb_diff_metrics(pillow, native)
    # The shared display list still names the historical Lanczos stages explicitly. Until the
    # native Pillow-Lanczos kernel lands, Skia uses Catmull-Rom under this measured fixture gate.
    assert mean <= 2.0, mean
    assert p99 <= 30, p99
    assert diff.getchannel("A").getbbox() is None


@pytest.mark.skipif(
    _native is None or getattr(_native, "IR_CAPABILITY", 0) < REQUIRED_NATIVE_IR_CAPABILITY,
    reason="UnitySubscene-capable native renderer is required",
)
@pytest.mark.skipif(not PAYLOAD_FILE.is_file(), reason="custom profile parity fixture not present")
def test_real_honor_deck_fixture_is_native_pixel_pure_and_matches_pillow(monkeypatch):
    from src.settings import settings

    request = _honor_deck_general_request()
    assert len(request.profile_context["userProfileHonors"]) == 3
    assert set(request.resources["profileHonorRequests"]) == {"profile:1", "profile:2", "profile:3"}
    assert request.resources["profileHonorRequests"]["profile:2"]["honor_type"] == "birthday"
    assert request.resources["profileHonorRequests"]["profile:3"]["honor_type"] == "birthday"
    assert request.resources["profileHonorRequests"]["profile:2"]["honor_img_path"].startswith("asset/jp-assets/")
    assert request.resources["profileHonorRequests"]["profile:3"]["honor_img_path"].startswith("asset/jp-assets/")
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
    assert payload.native_metrics["custom_profile_native_elements"] == 1
    assert payload.native_metrics["custom_profile_hybrid_elements"] == 0
    assert payload.native_metrics["custom_profile_mem_images"] == 0
    assert payload.native_metrics["custom_profile_mem_bytes"] == 0
    assert captured["mem_images"] == {}
    assert b"mem:" not in captured["ir_json"]

    scene = json.loads(captured["ir_json"])
    nodes = list(_walk_nodes(scene["root"]))
    assert sum(node["type"] == "UnitySubscene" for node in nodes) == 4

    native = Image.open(BytesIO(payload.image_bytes)).convert("RGBA")
    diff = ImageChops.difference(pillow, native)
    mean, p99 = _rgb_diff_metrics(pillow, native)
    assert mean <= 2.0, mean
    assert p99 <= 30, p99
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
