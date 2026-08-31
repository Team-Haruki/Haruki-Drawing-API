"""Pins the Skia path for /profile/custom-profile-card.

The custom profile card is a hand-built IR scene (an argued exemption in
test_route_render_contract.py): ``try_render_custom_profile_card_payload`` must follow the honor
doctrine — fail-open, exactly one /render-stats outcome per committed attempt, never raise — and
the route must fall back to the Pillow compose whenever the Skia path declines. A canonical
ValueError -> 400 is rejected input rather than committed render traffic.

The unit tests fake everything native. Only the final end-to-end test needs the built extension
(at the production ``REQUIRED_NATIVE_IR_CAPABILITY`` — the payload may exercise Transform,
SdfQuad/A8 and image blur, and the production loader rejects anything older) plus the real
parity payload, and skips when either is missing so CI without fixtures stays green.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
import json
import logging
import math
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from PIL import Image, ImageChops, ImageDraw
import pytest

try:
    import haruki_skia_renderer as _native
except ImportError:  # pragma: no cover - extension not built
    _native = None

from src.core.debug import current_render_backend, pop_request_context, push_request_context
from src.core.image_payload import EncodedImagePayload
from src.core.pillow_telemetry import (
    begin_pillow_touch_scope,
    end_pillow_touch_scope,
    take_pillow_touch_snapshot,
)
import src.core.pjsk.profile as route_mod
from src.sekai.profile.custom_profile.card_prefab import (
    CardCoverArtOp,
    CardDisplayList,
    PillowCardAdapter,
)
from src.sekai.profile.custom_profile.drawer import compose_custom_profile_card_image
from src.sekai.profile.custom_profile.renderer import (
    PROFILE_RENDER_VIEW_H,
    PROFILE_RENDER_VIEW_W,
    DirectSdfAtlasQuad,
    DirectSdfFontQuad,
    NativeContent,
    PNGRenderer,
)
import src.sekai.profile.custom_profile.skia as skia_mod
from src.sekai.profile.custom_profile.skia import (
    CUSTOM_PROFILE_ENDPOINT,
    _build_scene,
    try_render_custom_profile_card_payload,
)
from src.sekai.profile.model import CustomProfileCardRenderRequest
from src.sekai.skia_renderer.canvas import REQUIRED_NATIVE_IR_CAPABILITY
from src.sekai.skia_renderer.render_stats import (
    OUTCOME_ERROR,
    OUTCOME_FALLBACK,
    OUTCOME_SKIA,
    get_render_stats,
    reset_render_stats,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PAYLOAD_FILE = REPO_ROOT / "out" / "parity-payloads" / "custom_profile_card.json"


@pytest.fixture(autouse=True)
def _clean_stats():
    tokens = push_request_context("test", "/api/pjsk/profile/custom-profile-card", "POST")
    reset_render_stats()
    try:
        yield
    finally:
        reset_render_stats()
        pop_request_context(tokens)


def _request() -> CustomProfileCardRenderRequest:
    """Minimal VALID model (region defaults to cn); the render itself is stubbed in unit tests."""
    return CustomProfileCardRenderRequest(card={"customProfileCard": {}})


def _endpoint_stats() -> dict[str, int]:
    return get_render_stats()["endpoints"][CUSTOM_PROFILE_ENDPOINT]


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", (8, 8), (0, 128, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _native_subtree_from_builder(builder):
    scene = builder.build()
    return skia_mod.NativeSubtree(
        size=(builder.width, builder.height),
        nodes=tuple(scene["root"]["children"]),
        fonts=scene["fonts"],
        mem_images={},
        assets_base_dir=scene["assets_base_dir"],
    )


def _walk_ir_nodes(nodes):
    for node in nodes:
        yield node
        yield from _walk_ir_nodes(node.get("children", []))


def _local_rgb_diff_metrics(reference: Image.Image, rendered: Image.Image) -> tuple[float, int]:
    assert reference.size == rendered.size
    white = Image.new("RGBA", reference.size, (255, 255, 255, 255))
    reference_bbox = ImageChops.difference(reference, white).convert("RGB").getbbox()
    rendered_bbox = ImageChops.difference(rendered, white).convert("RGB").getbbox()
    assert reference_bbox is not None
    assert rendered_bbox is not None
    content_bbox = (
        min(reference_bbox[0], rendered_bbox[0]),
        min(reference_bbox[1], rendered_bbox[1]),
        max(reference_bbox[2], rendered_bbox[2]),
        max(reference_bbox[3], rendered_bbox[3]),
    )
    histogram = ImageChops.difference(reference, rendered).crop(content_bbox).convert("RGB").histogram()
    channel_pixels = (content_bbox[2] - content_bbox[0]) * (content_bbox[3] - content_bbox[1]) * 3
    mean = sum(value * histogram[channel * 256 + value] for channel in range(3) for value in range(256))
    mean /= channel_pixels
    threshold = channel_pixels * 0.99
    seen = 0
    for value in range(256):
        seen += sum(histogram[channel * 256 + value] for channel in range(3))
        if seen >= threshold:
            return mean, value
    return mean, 255


# ------------------------- fail-open outcomes (no native needed) -------------------------


def test_missing_native_extension_records_fallback(monkeypatch):
    """ImportError (missing wheel or stale capability) -> None + exactly one fallback."""
    monkeypatch.setattr(skia_mod, "skia_plot_enabled", lambda: True)
    persisted = []

    def _no_wheel():
        raise ImportError("haruki_skia_renderer not built")

    monkeypatch.setattr(skia_mod, "load_native_renderer", _no_wheel)
    monkeypatch.setattr(
        skia_mod,
        "persist_custom_profile_diagnostic",
        lambda **kwargs: persisted.append(kwargs) or True,
    )

    assert asyncio.run(try_render_custom_profile_card_payload(_request())) is None
    stats = _endpoint_stats()
    assert stats["fallback"] == 1
    assert stats["total"] == 1
    assert len(persisted) == 1
    assert persisted[0]["stage"] == "renderer_load"
    assert persisted[0]["error_type"] == "ImportError"


def test_disabled_gate_records_disabled_without_loading_native(monkeypatch):
    monkeypatch.setattr(skia_mod, "skia_plot_enabled", lambda: False)

    def _boom():  # pragma: no cover - must not run
        raise AssertionError("load_native_renderer must not run when the gate is off")

    monkeypatch.setattr(skia_mod, "load_native_renderer", _boom)

    assert asyncio.run(try_render_custom_profile_card_payload(_request())) is None
    stats = _endpoint_stats()
    assert stats["disabled"] == 1
    assert stats["total"] == 1


def test_pool_render_exception_is_contained_and_recorded(monkeypatch, caplog):
    """FAIL-OPEN: nothing escaping the pool render may propagate — the route depends on None to
    reach the Pillow compose that raises the canonical user-visible error.

    _build_scene is stubbed to raise, but with the minimal request the real PNGRenderer
    construction may fail first (missing region asset dirs on CI). Either way the whole pool task
    is inside the one broad try, so the contract is the same: return None, record exactly one
    error, and never hand the stubbed native renderer a scene.
    """
    monkeypatch.setattr(skia_mod, "skia_plot_enabled", lambda: True)

    class _Native:
        called = False

        def render_scene(self, *args, **kwargs):  # pragma: no cover - must not run
            _Native.called = True
            raise AssertionError("render_scene must not run when the scene build fails")

    monkeypatch.setattr(skia_mod, "load_native_renderer", lambda: _Native())

    def _explode(renderer, card):
        raise RuntimeError("scene assembly exploded")

    monkeypatch.setattr(skia_mod, "_build_scene", _explode)

    with caplog.at_level(logging.ERROR, logger="custom_profile.draw.perf"):
        assert asyncio.run(try_render_custom_profile_card_payload(_request())) is None
    stats = _endpoint_stats()
    assert stats["error"] == 1
    assert stats["total"] == 1
    assert sum(stats["errors_by_stage"].values()) == 1
    assert not _Native.called
    errors = [record for record in caplog.records if record.name == "custom_profile.draw.perf"]
    assert len(errors) == 1
    assert errors[0].levelno == logging.ERROR
    assert "committed_error stage=" in errors[0].message


def test_incomplete_visible_scene_declines_before_native_render(monkeypatch, tmp_path):
    from src.sekai.profile.custom_profile import drawer as drawer_mod

    monkeypatch.setattr(skia_mod, "skia_plot_enabled", lambda: True)
    monkeypatch.setattr(drawer_mod, "_require_region_path", lambda *args: tmp_path)
    monkeypatch.setattr(drawer_mod, "_optional_region_file", lambda *args: None)

    class _Native:
        called = False

        def render_scene(self, *args, **kwargs):  # pragma: no cover - must not run
            _Native.called = True
            raise AssertionError("an incomplete scene must not reach Rust")

    report = skia_mod.CustomProfileSceneReport(
        elements_total=1,
        visible_elements=1,
        missing_elements=1,
        classifications_by_kind={"stamp": {"missing": 1}},
    )
    monkeypatch.setattr(skia_mod, "load_native_renderer", lambda: _Native())
    monkeypatch.setattr(skia_mod, "_build_scene", lambda renderer, card: (b"{}", {}, report))

    assert asyncio.run(try_render_custom_profile_card_payload(_request())) is None
    stats = _endpoint_stats()
    assert stats["fallback"] == 1
    assert stats["scene_completeness"]["incomplete"] == 1
    assert stats["scene_completeness"]["missing_elements"] == 1
    assert not _Native.called


def test_legacy_sdf_quad_is_declined_without_mem_or_pillow_touch():
    token = begin_pillow_touch_scope()
    try:
        scene = skia_mod._SceneAssembler(skia_mod._new_builder(8, 8), (8, 8), 1024)
        scalars = SimpleNamespace(
            face_color=(255, 255, 255, 255),
            face_scale=1.0,
            face_w=0.5,
            alpha=1.0,
            underlay=None,
        )
        quad = SimpleNamespace(field=Image.new("L", (2, 2), 255), left=1, top=1, scalars=scalars)
        emitted = scene.emit_sdf_quads([quad])
        snapshot = take_pillow_touch_snapshot()
    finally:
        end_pillow_touch_scope(token)

    assert not emitted
    assert snapshot.counts == {}
    assert scene.mem_images == {}
    assert scene.builder.build()["root"]["children"] == []


def test_sdf_atlas_quad_emits_without_mem_or_pillow_touch(monkeypatch):
    monkeypatch.setattr(skia_mod, "_relative_asset_path", lambda path: "tmp/atlas.png")
    scalars = SimpleNamespace(
        face_color=(255, 240, 220),
        face_scale=1.25,
        face_w=0.4,
        alpha=0.75,
        underlay=None,
    )
    quad = DirectSdfAtlasQuad(
        atlas_path=Path("ignored.png"),
        atlas_size=(64, 64),
        crop=(-1, 2, 5, 8),
        field_size=(7, 9),
        size=(11, 13),
        affine=(1.0, 0.1, -0.25, -0.2, 0.9, 0.5),
        left=3,
        top=4,
        scalars=scalars,
    )

    token = begin_pillow_touch_scope()
    try:
        scene = skia_mod._SceneAssembler(skia_mod._new_builder(32, 32), (32, 32), 1024)
        assert scene.emit_sdf_quads([quad])
        snapshot = take_pillow_touch_snapshot()
    finally:
        end_pillow_touch_scope(token)

    assert snapshot.counts == {}
    assert scene.mem_images == {}
    node = scene.builder.build()["root"]["children"][0]
    assert node == {
        "type": "SdfAtlasQuad",
        "path": "tmp/atlas.png",
        "atlas_size": [64, 64],
        "crop": [-1, 2, 5, 8],
        "field_size": [7, 9],
        "pos": [3.0, 4.0],
        "size": [11, 13],
        "affine": [1.0, 0.1, -0.25, -0.2, 0.9, 0.5],
        "shading": {
            "face_color": [255, 240, 220],
            "face_scale": 1.25,
            "face_w": 0.4,
            "alpha": 0.75,
            "underlay": None,
        },
    }


def test_sdf_font_quad_emits_registered_font_without_mem_or_pillow_touch(tmp_path: Path, monkeypatch):
    font_path = tmp_path / "tmp" / "dynamic.ttf"
    font_path.parent.mkdir()
    font_path.touch()
    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", tmp_path)
    scalars = SimpleNamespace(
        face_color=(250, 240, 230),
        face_scale=1.5,
        face_w=0.35,
        alpha=0.8,
        underlay=None,
    )
    quad = DirectSdfFontQuad(
        font_path=font_path,
        codepoint=ord("A"),
        sample_size=64.0,
        bbox=(-2, -48, 40, 8),
        padding=6,
        crop_padding=3,
        field_size=(42, 56),
        spread=4.9,
        size=(48, 60),
        affine=(1.0, 0.1, -0.25, -0.2, 0.9, 0.5),
        left=3,
        top=4,
        scalars=scalars,
    )

    token = begin_pillow_touch_scope()
    try:
        scene = skia_mod._SceneAssembler(skia_mod._new_builder(80, 80), (80, 80), 1024)
        assert scene.emit_sdf_quads([quad])
        snapshot = take_pillow_touch_snapshot()
    finally:
        end_pillow_touch_scope(token)

    assert snapshot.counts == {}
    assert scene.mem_images == {}
    built = scene.builder.build()
    node = built["root"]["children"][0]
    font_name = node["font"]["name"]
    assert built["fonts"]["extra"] == {font_name: str(font_path.resolve())}
    assert node == {
        "type": "SdfFontQuad",
        "font": {"role": "default", "name": font_name, "size": 64.0},
        "codepoint": ord("A"),
        "bbox": [-2, -48, 40, 8],
        "padding": 6,
        "crop_padding": 3,
        "field_size": [42, 56],
        "spread": 4.9,
        "pos": [3.0, 4.0],
        "size": [48, 60],
        "affine": [1.0, 0.1, -0.25, -0.2, 0.9, 0.5],
        "shading": {
            "face_color": [250, 240, 230],
            "face_scale": 1.5,
            "face_w": 0.35,
            "alpha": 0.8,
            "underlay": None,
        },
    }


def test_scene_declines_unsupported_content_without_rendering_pillow_layers():
    contents = [
        NativeContent(
            layer=index,
            kind="general",
            item={},
            object_data={"visible": True},
        )
        for index in range(2)
    ]

    class _Renderer:
        def native_card_ref(self, card):
            return {}

        def build_native_contents(self, card):
            return contents

        def render_content_for_card(self, content):  # pragma: no cover - must not run
            raise AssertionError("Skia scene assembly must not invoke the Pillow renderer")

        def record_native_audit(self, *args):
            return None

    _, mem_images, report = _build_scene(_Renderer(), {})

    assert mem_images == {}
    assert not report.complete
    assert report.hybrid_elements == 0
    assert report.unresolved_elements == 2
    assert report.metrics()["classifications_by_kind"] == {"general": {"unresolved": 2}}


def test_native_content_result_classifies_hidden_content_without_emitters():
    content = NativeContent(layer=1, kind="general", item={}, object_data={"visible": False})

    assert skia_mod._native_content_result(object(), content, object()) == ("hidden", "hidden")


def test_native_content_result_declines_quads_rejected_by_scene(monkeypatch):
    content = NativeContent(layer=1, kind="text", item={}, object_data={"visible": True})
    for name in (
        "_emit_native_asset_image",
        "_emit_native_omikuji_collection",
        "_emit_native_shape",
        "_emit_native_card_member",
        "_emit_native_honor",
        "_emit_native_simple_tmp_text",
    ):
        monkeypatch.setattr(skia_mod, name, lambda *_args: False)
    monkeypatch.setattr(skia_mod, "_emit_native_card_general", lambda *_args: None)
    monkeypatch.setattr(skia_mod, "_emit_native_honor_deck", lambda *_args: None)
    monkeypatch.setattr(skia_mod, "_emit_native_general", lambda *_args: None)
    monkeypatch.setattr(skia_mod, "_is_empty_text_noop", lambda *_args: False)
    monkeypatch.setattr(skia_mod, "_direct_text_quads", lambda *_args: [object()])

    class _Scene:
        def emit_sdf_quads(self, _quads):
            return False

    assert skia_mod._native_content_result(object(), content, _Scene()) is None


def test_scene_routes_non_decorative_tmp_through_sparse_native_quads(monkeypatch):
    content = NativeContent(
        layer=1,
        kind="text",
        item={"text": "<color=#ffffff>ordinary rich text"},
        object_data={"visible": True},
    )
    quad = object()
    emitted = []

    class _Renderer:
        text_layout = "tmp"
        tmp_text_render_mode = "sdf"

        def __init__(self):
            self.direct_calls = []
            self.audit = []

        def native_card_ref(self, card):
            return {}

        def build_native_contents(self, card):
            return [content]

        def prepare_direct_sdf_quads(self, item, object_data, **kwargs):
            self.direct_calls.append((item, object_data, kwargs))
            return [quad]

        def render_content_for_card(self, content):  # pragma: no cover - must not run
            raise AssertionError("ordinary rich TMP must try the sparse native path first")

        def record_native_audit(self, *args):
            self.audit.append(args)

    monkeypatch.setattr(skia_mod, "build_simple_tmp_text_display_list", lambda *_args: None)
    monkeypatch.setattr(
        skia_mod._SceneAssembler,
        "emit_sdf_quads",
        lambda _scene, quads: emitted.extend(quads) or True,
    )
    renderer = _Renderer()

    _, mem_images, report = _build_scene(renderer, {})

    assert mem_images == {}
    assert renderer.direct_calls == [
        (
            content.item,
            content.object_data,
            {
                "defer_static_atlas": True,
                "defer_dynamic_font": True,
                "source_metrics_only": True,
            },
        )
    ]
    assert emitted == [quad]
    assert renderer.audit[-1][2:] == ("rendered-direct", None)
    assert report.complete
    assert report.native_elements == 1
    assert report.noop_elements == 0
    assert report.hybrid_elements == 0
    assert report.metrics()["classifications_by_kind"] == {"text": {"native": 1}}


def test_scene_classifies_raw_whitespace_text_as_native_noop(monkeypatch):
    content = NativeContent(
        layer=1,
        kind="text",
        item={"text": "   \n\t"},
        object_data={"visible": True},
    )

    class _Renderer:
        def native_card_ref(self, card):
            return {}

        def build_native_contents(self, card):
            return [content]

        def generate_text_data(self, item):
            return SimpleNamespace(text=item["text"])

        def render_content_for_card(self, content):  # pragma: no cover - must not run
            raise AssertionError("whitespace-only text must not enter the Pillow renderer")

        def record_native_audit(self, *args):
            return None

    monkeypatch.setattr(skia_mod, "build_simple_tmp_text_display_list", lambda *_args: None)

    _, mem_images, report = _build_scene(_Renderer(), {})

    assert mem_images == {}
    assert report.complete
    assert report.noop_elements == 1
    assert report.missing_elements == 0
    assert report.metrics()["classifications_by_kind"] == {"text": {"noop": 1}}


def test_scene_oversized_tmp_text_uses_sparse_native_quads_before_pillow(monkeypatch):
    content = NativeContent(
        layer=1,
        kind="text",
        item={"text": "<line-indent=98.4%><rotate=90>A"},
        object_data={"visible": True},
    )

    class _Renderer:
        tmp_decorative_direct_raster = True
        text_layout = "tmp"
        tmp_text_render_mode = "sdf"

        def __init__(self):
            self.direct_calls = []
            self.audit = []

        def native_card_ref(self, card):
            return {}

        def build_native_contents(self, card):
            return [content]

        def render_content_for_card(self, content):  # pragma: no cover - must not run
            raise AssertionError("sparse TMP preflight must happen before Pillow rendering")

        def prepare_direct_sdf_quads(self, item, object_data, **kwargs):
            self.direct_calls.append((item, object_data, kwargs))
            return []

        def record_native_audit(self, *args):
            self.audit.append(args)

    monkeypatch.setattr(skia_mod, "build_simple_tmp_text_display_list", lambda *_args: None)
    renderer = _Renderer()

    _, mem_images, report = _build_scene(renderer, {})

    assert mem_images == {}
    assert renderer.direct_calls == [
        (
            content.item,
            content.object_data,
            {
                "defer_static_atlas": True,
                "defer_dynamic_font": True,
                "source_metrics_only": True,
            },
        )
    ]
    assert renderer.audit[-1][2:] == ("rendered-direct", None)
    assert report.complete
    assert report.noop_elements == 1


def test_sdf_shape_lowers_to_asset_node_without_pillow_raster(tmp_path, monkeypatch):
    shape_path = tmp_path / "shape" / "round.png"
    shape_path.parent.mkdir()
    shape_path.touch()
    content = NativeContent(
        layer=1,
        kind="shape",
        item={
            "id": 1,
            "colorId": 2,
            "outlineColorId": 3,
            "alpha": 0.8,
            "outlineAlpha": 0.6,
            "outlineSize": 0.2,
        },
        object_data={
            "visible": True,
            "position": {"x": 10, "y": 20},
            "scale": {"x": 1.5, "y": 0.75},
            "rotation": {"z": 0, "w": 1},
        },
    )
    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", tmp_path)

    class _Renderer:
        shape_outline_mode = "sdf"
        shape_sdf_screen_fwidth = True
        triangle_mode = "asset"
        shape_sdf_ratio_scale = 1.0
        shape_sdf_outer_factor = 1.0
        shape_sdf_face_factor = -0.475
        shape_sdf_softness = 0.0
        shape_sdf_source = "rgb"
        rotation_sign = -1
        position_scale_x = 1.1
        position_scale_y = 1.2

        def __init__(self):
            self.shapes = {1: {"fileName": "round"}}
            self.colors = {2: "#112233", 3: "#aabbcc"}

        def native_card_ref(self, card):
            return {}

        def build_native_contents(self, card):
            return [content]

        def shape_resource_path(self, resource):
            return shape_path

        def unity_point(self, position):
            return (100.0, 200.0)

        def record_native_audit(self, *args):
            return None

        def render_content_for_card(self, content):  # pragma: no cover - must not run
            raise AssertionError("eligible SDF shape must not enter the Pillow renderer")

    ir_json, mem_images, report = _build_scene(_Renderer(), {})
    scene = json.loads(ir_json)
    nodes = scene["root"]["children"]
    shape = next(node for node in nodes if node["type"] == "SdfShape")

    assert mem_images == {}
    assert shape["path"] == "shape/round.png"
    assert shape["sdf_scale"] == [1.5, 0.75]
    assert shape["post_scale"] == [1.1, 1.2]
    assert shape["fill_color"] == [17, 34, 51]
    assert report.complete
    assert report.native_elements == 1


def test_static_image_lowers_to_unity_asset_node_without_pillow_raster(tmp_path, monkeypatch):
    asset_path = tmp_path / "background.png"
    asset_path.touch()
    content = NativeContent(
        layer=1,
        kind="story_background",
        item={"id": 5},
        object_data={
            "visible": True,
            "position": {"x": -10, "y": 20},
            "scale": {"x": 0.8, "y": 1.2},
            "rotation": {"z": 0, "w": 1},
        },
    )
    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", tmp_path)

    class _Renderer:
        rotation_sign = -1
        position_scale_x = 1.1
        position_scale_y = 1.2

        def native_card_ref(self, card):
            return {}

        def build_native_contents(self, card):
            return [content]

        def image_resource_for(self, kind, item):
            return {"imagePath": "background.png"}

        def resource_path(self, resource):
            return asset_path

        def unity_point(self, position):
            return (100.0, 200.0)

        def record_native_audit(self, *args):
            return None

        def render_content_for_card(self, content):  # pragma: no cover - must not run
            raise AssertionError("static asset must not enter the Pillow renderer")

    ir_json, mem_images, report = _build_scene(_Renderer(), {})
    scene = json.loads(ir_json)
    image = next(node for node in scene["root"]["children"] if node["type"] == "UnityImage")

    assert mem_images == {}
    assert image["path"] == "background.png"
    assert image["object_scale"] == [0.8, 1.2]
    assert report.complete
    assert report.native_elements == 1


@pytest.mark.skipif(
    _native is None or getattr(_native, "IR_CAPABILITY", 0) < REQUIRED_NATIVE_IR_CAPABILITY,
    reason="current native renderer is required",
)
@pytest.mark.parametrize(
    "kind",
    [
        "general_background",
        "story_background",
        "stand_member",
        "collection",
        "other",
        "character_icon",
        "material",
        "user_interface_icon",
        "stamp",
    ],
)
def test_static_category_only_fixture_is_native_pixel_pure_with_transform(kind, tmp_path, monkeypatch):
    """Every direct-image category uses the same asset-only scene path.

    This deliberately builds one category element rather than retaining a complete profile
    request.  The transparent source, non-uniform downscale and rotation cover the operations
    that the two existing whole-card fixtures do not exercise for every image bucket.
    """

    asset_path = tmp_path / "category" / f"{kind}.png"
    asset_path.parent.mkdir(parents=True)
    source = Image.new("RGBA", (71, 53), (0, 0, 0, 0))
    draw = ImageDraw.Draw(source)
    draw.rounded_rectangle((2, 3, 62, 47), radius=9, fill=(34, 126, 211, 181), outline=(245, 92, 61, 255), width=3)
    draw.polygon(((8, 42), (34, 7), (67, 45)), fill=(242, 205, 72, 137))
    source.save(asset_path)
    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", tmp_path)

    angle = 17.0
    object_data = {
        "visible": True,
        "position": {"x": 137.25, "y": -69.75},
        "scale": {"x": 0.63, "y": 0.81},
        "rotation": {"z": math.sin(math.radians(angle) / 2.0), "w": math.cos(math.radians(angle) / 2.0)},
    }
    content = NativeContent(layer=1, kind=kind, item={"id": 1}, object_data=object_data)

    class _Renderer:
        rotation_sign = 1
        position_scale_x = PROFILE_RENDER_VIEW_W / 1830.0
        position_scale_y = PROFILE_RENDER_VIEW_H / 813.0
        origin_x = PROFILE_RENDER_VIEW_W / 2.0
        origin_y = PROFILE_RENDER_VIEW_H / 2.0

        def __init__(self):
            self.stamp_assets = {1: {"imagePath": asset_path.as_posix()}}

        def general_font_path(self):
            return None

        def native_card_ref(self, card):
            return {}

        def build_native_contents(self, card):
            return [content]

        def image_resource_for(self, content_kind, item):
            return {"imagePath": asset_path.as_posix()}

        def resource_path(self, resource):
            return asset_path

        def resolve_request_asset_path(self, raw_path):
            return asset_path if Path(raw_path) == asset_path else None

        def unity_point(self, position):
            return (
                self.origin_x + float(position.get("x", 0.0)) * self.position_scale_x,
                self.origin_y - float(position.get("y", 0.0)) * self.position_scale_y,
            )

        def record_native_audit(self, *args):
            return None

        def render_content_for_card(self, content):  # pragma: no cover - must not run
            raise AssertionError("eligible direct-image content must not enter the Pillow renderer")

    renderer = _Renderer()
    token = begin_pillow_touch_scope()
    try:
        ir_json, mem_images, report = _build_scene(renderer, {})
        native_result = _native.render_scene(ir_json, mem_images)
        pillow_touches = take_pillow_touch_snapshot()
    finally:
        end_pillow_touch_scope(token)

    assert report.complete
    assert report.classifications_by_kind == {kind: {"native": 1}}
    assert mem_images == {}
    assert b"mem:" not in ir_json
    assert pillow_touches.counts == {}

    native_payload = skia_mod.payload_from_native(native_result)
    native_image = Image.open(BytesIO(native_payload.image_bytes)).convert("RGBA")

    transformer = object.__new__(PNGRenderer)
    transformer.position_scale_x = renderer.position_scale_x
    transformer.position_scale_y = renderer.position_scale_y
    transformer.origin_x = renderer.origin_x
    transformer.origin_y = renderer.origin_y
    transformer.rotation_sign = renderer.rotation_sign
    transformer.canvas_w = int(PROFILE_RENDER_VIEW_W)
    transformer.canvas_h = int(PROFILE_RENDER_VIEW_H)
    transformer.clip_canvas_transform = True
    transformer.max_layer_pixels = 8 * 1024 * 1024
    transformer.premultiply_alpha_transforms = False
    prepared = transformer.prepare_transformed_layer(
        (source, (source.width / 2.0, source.height / 2.0)),
        object_data,
        kind,
        False,
    )
    assert prepared is not None
    pillow_image = Image.new(
        "RGBA",
        (int(PROFILE_RENDER_VIEW_W), int(PROFILE_RENDER_VIEW_H)),
        (255, 255, 255, 255),
    )
    pillow_image.alpha_composite(prepared.image, prepared.xy)

    mean, p99 = _local_rgb_diff_metrics(pillow_image, native_image)

    # Rotated Custom Profile layers intentionally use one native matrix pass instead of
    # Pillow's resize + rotate + supersample pipeline. This adversarial transparent fixture
    # establishes the element-local relaxed budget; the whole-card release budget remains
    # tighter because unchanged white pixels are included there.
    assert mean <= 3.1, (kind, mean, p99)
    assert p99 <= 33, (kind, mean, p99)


@pytest.mark.skipif(
    _native is None or getattr(_native, "IR_CAPABILITY", 0) < REQUIRED_NATIVE_IR_CAPABILITY,
    reason="current native renderer is required",
)
@pytest.mark.parametrize(
    ("card_kind", "native_size", "cover_size", "crop_align", "blend"),
    [
        pytest.param("full", (220, 124), (220.0, 124.0), (0.5, 0.5), "src", id="full"),
        pytest.param("deck", (96, 132), (96.0, 158.0), (0.5, 0.0), "src_over", id="clip"),
    ],
)
def test_card_member_category_only_fixture_is_native_pixel_pure(
    card_kind,
    native_size,
    cover_size,
    crop_align,
    blend,
    tmp_path,
    monkeypatch,
):
    """Top-level full and clip card members need no whole-profile fixture."""

    asset_path = tmp_path / "category" / f"card_member_{card_kind}.png"
    asset_path.parent.mkdir(parents=True)
    source = Image.new("RGBA", (137, 181), (0, 0, 0, 0))
    draw = ImageDraw.Draw(source)
    draw.rectangle((4, 6, 130, 175), fill=(36, 102, 191, 218))
    draw.ellipse((18, 22, 119, 123), fill=(248, 194, 72, 181), outline=(253, 92, 104, 255), width=4)
    draw.polygon(((9, 169), (68, 77), (128, 169)), fill=(72, 220, 173, 147))
    source.save(asset_path)
    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", tmp_path)

    display_list = CardDisplayList(
        card_kind,
        native_size,
        (CardCoverArtOp(asset_path, cover_size, crop_align=crop_align, blend=blend),),
    )
    angle = -13.0
    object_data = {
        "visible": True,
        "position": {"x": -91.5, "y": 63.25},
        "scale": {"x": 0.72, "y": 0.58},
        "rotation": {"z": math.sin(math.radians(angle) / 2.0), "w": math.cos(math.radians(angle) / 2.0)},
    }
    content = NativeContent(layer=1, kind="card_member", item={"id": 1}, object_data=object_data)

    class _Renderer:
        rotation_sign = 1
        position_scale_x = PROFILE_RENDER_VIEW_W / 1830.0
        position_scale_y = PROFILE_RENDER_VIEW_H / 813.0
        origin_x = PROFILE_RENDER_VIEW_W / 2.0
        origin_y = PROFILE_RENDER_VIEW_H / 2.0

        def general_font_path(self):
            return None

        def native_card_ref(self, card):
            return {}

        def build_native_contents(self, card):
            return [content]

        def build_card_member_display_list(self, item):
            return display_list

        def unity_point(self, position):
            return (
                self.origin_x + float(position.get("x", 0.0)) * self.position_scale_x,
                self.origin_y - float(position.get("y", 0.0)) * self.position_scale_y,
            )

        def record_native_audit(self, *args):
            return None

        def render_content_for_card(self, content):  # pragma: no cover - must not run
            raise AssertionError("eligible card member must not enter the Pillow renderer")

    renderer = _Renderer()
    token = begin_pillow_touch_scope()
    try:
        ir_json, mem_images, report = _build_scene(renderer, {})
        native_result = _native.render_scene(ir_json, mem_images)
        pillow_touches = take_pillow_touch_snapshot()
    finally:
        end_pillow_touch_scope(token)

    assert report.complete
    assert report.classifications_by_kind == {"card_member": {"native": 1}}
    assert mem_images == {}
    assert b"mem:" not in ir_json
    assert pillow_touches.counts == {}

    native_payload = skia_mod.payload_from_native(native_result)
    native_image = Image.open(BytesIO(native_payload.image_bytes)).convert("RGBA")
    adapter = PillowCardAdapter(
        lambda size, bold: None,
        lambda *args, **kwargs: False,
        lambda name: None,
        lambda path: Image.open(path).convert("RGBA") if path == asset_path else None,
    )
    card_image = adapter.render(display_list)
    transformer = object.__new__(PNGRenderer)
    transformer.position_scale_x = renderer.position_scale_x
    transformer.position_scale_y = renderer.position_scale_y
    transformer.origin_x = renderer.origin_x
    transformer.origin_y = renderer.origin_y
    transformer.rotation_sign = renderer.rotation_sign
    transformer.canvas_w = int(PROFILE_RENDER_VIEW_W)
    transformer.canvas_h = int(PROFILE_RENDER_VIEW_H)
    transformer.clip_canvas_transform = True
    transformer.max_layer_pixels = 8 * 1024 * 1024
    transformer.premultiply_alpha_transforms = False
    prepared = transformer.prepare_transformed_layer(
        (card_image, (card_image.width / 2.0, card_image.height / 2.0)),
        object_data,
        "card_member",
        False,
    )
    assert prepared is not None
    pillow_image = Image.new(
        "RGBA",
        (int(PROFILE_RENDER_VIEW_W), int(PROFILE_RENDER_VIEW_H)),
        (255, 255, 255, 255),
    )
    pillow_image.alpha_composite(prepared.image, prepared.xy)
    mean, p99 = _local_rgb_diff_metrics(pillow_image, native_image)

    assert mean <= 3.5, (card_kind, mean, p99)
    assert p99 <= 40, (card_kind, mean, p99)


@pytest.mark.parametrize("honor_type", ["normal", "birthday"])
def test_unsupported_element_cannot_allocate_mem_before_native_honor(
    honor_type,
    tmp_path,
    monkeypatch,
):
    import src.sekai.skia_renderer.canvas as canvas_mod

    base_path = tmp_path / f"{honor_type}_base.png"
    frame_path = tmp_path / f"{honor_type}_frame.png"
    Image.new("RGBA", (100, 40), (30, 80, 160, 255)).save(base_path)
    Image.new("RGBA", (100, 40), (255, 255, 255, 32)).save(frame_path)
    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(canvas_mod, "ASSETS_BASE_DIR", tmp_path)

    class _NativeInfo:
        ASSET_INFO_CAPABILITY = 1

        @staticmethod
        def asset_image_info(base, relative):
            path = (Path(base) / relative).resolve()
            stat = path.stat()
            return {
                "width": 100,
                "height": 40,
                "mode": "RGBA",
                "mtime_ns": stat.st_mtime_ns,
                "file_size": stat.st_size,
            }

    monkeypatch.setattr(skia_mod, "load_native_renderer", lambda: _NativeInfo())

    unsupported = NativeContent(
        layer=1,
        kind="card_member",
        item={"id": 9},
        object_data={"visible": True},
    )
    honor = NativeContent(
        layer=2,
        kind="honor",
        item={"id": 123, "fullSize": False},
        object_data={
            "visible": True,
            "position": {"x": 5, "y": 6},
            "scale": {"x": 0.75, "y": 1.25},
            "rotation": {"z": 0, "w": 1},
        },
    )

    class _Renderer:
        rotation_sign = -1
        position_scale_x = 1.1
        position_scale_y = 1.2
        tmp_decorative_alpha_harden = 1.0

        def __init__(self):
            self.honor_requests = {}

        def native_card_ref(self, card):
            return {}

        def build_native_contents(self, card):
            return [unsupported, honor]

        def user_honor_level_for(self, honor_id):
            assert honor_id == 123
            return 2

        def honor_slot_key(self, honor_id, level, full_size):
            return f"{honor_id}:{level}:{'main' if full_size else 'sub'}"

        def build_masterdata_honor_request(self, honor_id, level, full_size):
            assert (honor_id, level, full_size) == (123, 2, False)
            return skia_mod.HonorRequest(
                honor_type=honor_type,
                honor_level=2,
                is_main_honor=False,
                honor_img_path=base_path.as_posix(),
                frame_img_path=frame_path.as_posix(),
            )

        def resolve_request_asset_path(self, raw_path):
            path = Path(raw_path)
            return path.resolve() if path.is_file() else None

        def unity_point(self, position):
            return (100.0, 200.0)

        def render_content_for_card(self, content):  # pragma: no cover - must not run
            raise AssertionError("Skia scene assembly must not invoke the Pillow renderer")

        def record_native_audit(self, *args):
            return None

    token = begin_pillow_touch_scope()
    try:
        ir_json, mem_images, report = _build_scene(_Renderer(), {})
        pillow_touches = take_pillow_touch_snapshot()
    finally:
        end_pillow_touch_scope(token)
    scene = json.loads(ir_json)
    subscene = next(node for node in scene["root"]["children"] if node["type"] == "UnitySubscene")
    subscene_paths = {node["path"] for node in _walk_ir_nodes(subscene["children"]) if node["type"] == "Image"}

    assert mem_images == {}
    assert subscene["size"] == [100, 40]
    assert subscene["object_scale"] == [0.75, 1.25]
    assert subscene["post_scale"] == [1.1, 1.2]
    assert f"{honor_type}_base.png" in subscene_paths
    assert f"{honor_type}_frame.png" in subscene_paths
    assert pillow_touches.counts == {}
    assert not report.complete
    assert report.native_elements == 1
    assert report.hybrid_elements == 0
    assert report.unresolved_elements == 1
    assert report.metrics()["classifications_by_kind"] == {
        "card_member": {"unresolved": 1},
        "honor": {"native": 1},
    }


def test_old_native_wheel_declines_honor_without_pillow_header_probe(tmp_path, monkeypatch):
    asset_path = tmp_path / "badge.png"
    Image.new("RGBA", (17, 9), (20, 40, 80, 255)).save(asset_path)
    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(
        skia_mod,
        "load_native_renderer",
        lambda: SimpleNamespace(IR_CAPABILITY=REQUIRED_NATIVE_IR_CAPABILITY),
    )

    token = begin_pillow_touch_scope()
    try:
        with pytest.raises(skia_mod._NativeAssetInfoUnavailable, match="asset-info"):
            skia_mod._header_only_asset_ref(asset_path, "badge.png")
        snapshot = take_pillow_touch_snapshot()
    finally:
        end_pillow_touch_scope(token)

    assert snapshot.native_purity == "pure"
    assert snapshot.counts == {}


@pytest.mark.skipif(
    _native is None or getattr(_native, "ASSET_INFO_CAPABILITY", 0) < 1,
    reason="native asset image-info API is required",
)
def test_native_asset_info_is_root_confined_and_pillow_free(tmp_path, monkeypatch):
    asset_path = tmp_path / "badge.png"
    Image.new("RGBA", (17, 9), (20, 40, 80, 255)).save(asset_path)
    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", tmp_path)

    token = begin_pillow_touch_scope()
    try:
        ref = skia_mod._header_only_asset_ref(asset_path, "badge.png")
        snapshot = take_pillow_touch_snapshot()
    finally:
        end_pillow_touch_scope(token)

    assert ref.size == (17, 9)
    assert ref.file_size == asset_path.stat().st_size
    assert snapshot.native_purity == "pure"
    assert snapshot.counts == {}
    with pytest.raises(ValueError, match="relative"):
        _native.asset_image_info(str(tmp_path), asset_path.as_posix())
    with pytest.raises(ValueError, match="unsupported component"):
        _native.asset_image_info(str(tmp_path), "../badge.png")


def test_native_honor_declines_when_a_supplied_overlay_is_missing(tmp_path, monkeypatch):
    import src.sekai.skia_renderer.canvas as canvas_mod

    base_path = tmp_path / "base.png"
    Image.new("RGBA", (100, 40), (30, 80, 160, 255)).save(base_path)
    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(canvas_mod, "ASSETS_BASE_DIR", tmp_path)
    honor = NativeContent(
        layer=1,
        kind="honor",
        item={"id": 123, "fullSize": False},
        object_data={
            "visible": True,
            "position": {"x": 0, "y": 0},
            "scale": {"x": 1, "y": 1},
            "rotation": {"z": 0, "w": 1},
        },
    )

    class _Renderer:
        rotation_sign = -1
        position_scale_x = 1.0
        position_scale_y = 1.0
        tmp_decorative_alpha_harden = 1.0

        def __init__(self):
            self.honor_requests = {
                "123:2:sub": {
                    "honor_type": "normal",
                    "honor_level": 2,
                    "honor_img_path": base_path.as_posix(),
                    "frame_img_path": (tmp_path / "missing-frame.png").as_posix(),
                }
            }

        def native_card_ref(self, card):
            return {}

        def build_native_contents(self, card):
            return [honor]

        def user_honor_level_for(self, honor_id):
            return 2

        def honor_slot_key(self, honor_id, level, full_size):
            return f"{honor_id}:{level}:{'main' if full_size else 'sub'}"

        def build_masterdata_honor_request(self, honor_id, level, full_size):
            pytest.fail("an explicit hybrid candidate must stop before masterdata fallback")

        def resolve_request_asset_path(self, raw_path):
            path = Path(raw_path)
            return path.resolve() if path.is_file() else None

        def render_content_for_card(self, content):  # pragma: no cover - must not run
            raise AssertionError("failed native honor preflight must decline before Pillow")

        def record_native_audit(self, *args):
            return None

    ir_json, mem_images, report = _build_scene(_Renderer(), {})
    nodes = json.loads(ir_json)["root"]["children"]

    assert not any(node["type"] == "UnitySubscene" for node in nodes)
    assert mem_images == {}
    assert not report.complete
    assert report.native_elements == 0
    assert report.unresolved_elements == 1


def test_native_bonds_honor_embeds_asset_backed_subtree_without_mem(tmp_path, monkeypatch):
    import src.sekai.skia_renderer.canvas as canvas_mod

    paths = {
        "left": tmp_path / "left.png",
        "right": tmp_path / "right.png",
        "one": tmp_path / "one.png",
        "two": tmp_path / "two.png",
        "mask": tmp_path / "mask.png",
    }
    for name in ("left", "right", "mask"):
        Image.new("RGBA", (180, 80), (30, 80, 160, 255)).save(paths[name])
    for name in ("one", "two"):
        Image.new("RGBA", (100, 100), (180, 80, 30, 192)).save(paths[name])
    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(canvas_mod, "ASSETS_BASE_DIR", tmp_path)

    class _NativeInfo:
        ASSET_INFO_CAPABILITY = 1

        @staticmethod
        def asset_image_info(base, relative):
            path = (Path(base) / relative).resolve()
            stat = path.stat()
            with Image.open(path) as image:
                width, height = image.size
                mode = image.mode
            return {
                "width": width,
                "height": height,
                "mode": mode,
                "mtime_ns": stat.st_mtime_ns,
                "file_size": stat.st_size,
            }

    monkeypatch.setattr(skia_mod, "load_native_renderer", lambda: _NativeInfo())

    content = NativeContent(
        layer=1,
        kind="bonds_honor",
        item={"id": 456, "fullSize": False, "wordId": 0},
        object_data={
            "visible": True,
            "position": {"x": 0, "y": 0},
            "scale": {"x": 1, "y": 1},
            "rotation": {"z": 0, "w": 1},
        },
    )

    class _Renderer:
        rotation_sign = -1
        position_scale_x = 1.0
        position_scale_y = 1.0

        def __init__(self):
            self.bonds_honor_requests = {}

        def user_bonds_honor_level_for(self, honor_id):
            assert honor_id == 456
            return 3

        def bonds_honor_slot_key(
            self,
            honor_id,
            level,
            full_size,
            word_id,
            inverse,
            use_unit_virtual_singer=False,
        ):
            assert not use_unit_virtual_singer
            return f"{honor_id}:{level}:{'main' if full_size else 'sub'}:{word_id}:{'reverse' if inverse else 'normal'}"

        def build_masterdata_bonds_honor_request(self, item, full_size):
            assert item == content.item
            assert not full_size
            return skia_mod.HonorRequest(
                honor_type="bonds",
                honor_level=3,
                bonds_bg_path=paths["left"].as_posix(),
                bonds_bg_path2=paths["right"].as_posix(),
                chara_icon_path=paths["one"].as_posix(),
                chara_icon_path2=paths["two"].as_posix(),
                mask_img_path=paths["mask"].as_posix(),
            )

        def resolve_request_asset_path(self, raw_path):
            path = Path(raw_path)
            return path.resolve() if path.is_file() else None

        def unity_point(self, position):
            return 160.0, 90.0

    builder = skia_mod._new_builder(320, 180)
    scene = skia_mod._SceneAssembler(builder, (320, 180), 8 * 1024 * 1024)

    assert skia_mod._emit_native_honor(_Renderer(), content, scene)
    assert scene.mem_images == {}
    subscene = builder.build()["root"]["children"][0]
    assert subscene["type"] == "UnitySubscene"

    images = [node for node in _walk_ir_nodes(subscene["children"]) if node["type"] == "Image"]
    assert images
    assert all(not image["path"].startswith("mem:") for image in images)
    assert any(image.get("blend") == "paste_lerp" for image in images)


def test_native_honor_declines_invalid_scale_before_mutating_scene(monkeypatch):
    badge_builder = skia_mod._new_builder(180, 80)
    badge_builder.rect((0, 0), (180, 80), fill=(20, 40, 80, 255))
    badge = _native_subtree_from_builder(badge_builder)
    request = skia_mod.HonorRequest(honor_type="normal")
    monkeypatch.setattr(skia_mod, "_native_honor_candidates", lambda renderer, content: iter([request]))
    monkeypatch.setattr(skia_mod, "_lower_native_honor_request", lambda renderer, value: ("ready", badge))

    class _Renderer:
        rotation_sign = -1
        position_scale_x = 1.0
        position_scale_y = 1.0

        @staticmethod
        def unity_point(position):
            return 10.0, 10.0

    builder = skia_mod._new_builder(100, 100)
    scene = skia_mod._SceneAssembler(builder, (100, 100), 1024)
    content = NativeContent(1, "honor", {}, {"visible": True, "scale": {"x": -1, "y": 1}})

    assert not skia_mod._emit_native_honor(_Renderer(), content, scene)
    assert builder.build()["root"]["children"] == []


def test_native_honor_decline_paths_leave_scene_untouched(monkeypatch):
    builder = skia_mod._new_builder(100, 100)
    scene = skia_mod._SceneAssembler(builder, (100, 100), 1024)
    content = NativeContent(1, "honor", {}, {"visible": False})

    assert not skia_mod._emit_native_honor(SimpleNamespace(), content, scene)

    content.object_data["visible"] = True
    candidate_result = None
    monkeypatch.setattr(skia_mod, "_native_honor_candidates", lambda renderer, value: candidate_result)
    assert not skia_mod._emit_native_honor(SimpleNamespace(), content, scene)

    candidate_result = iter([None, object()])
    assert not skia_mod._emit_native_honor(SimpleNamespace(), content, scene)

    request = skia_mod.HonorRequest(honor_type="normal")
    candidate_result = iter([request])
    monkeypatch.setattr(skia_mod, "_lower_native_honor_request", lambda renderer, value: ("unrenderable", None))
    assert not skia_mod._emit_native_honor(SimpleNamespace(), content, scene)

    candidate_result = iter([request])
    monkeypatch.setattr(skia_mod, "_lower_native_honor_request", lambda renderer, value: ("hybrid", None))
    assert not skia_mod._emit_native_honor(SimpleNamespace(), content, scene)
    assert builder.build()["root"]["children"] == []


def test_native_profile_honor_badge_classifies_candidate_outcomes(monkeypatch):
    keys = SimpleNamespace(profile_keys=("profile", "duplicate"), ordinary_keys=("ordinary",))
    monkeypatch.setattr(skia_mod, "honor_deck_request_candidates", lambda **kwargs: keys)
    renderer = SimpleNamespace(profile_honor_requests={}, honor_requests={})

    assert skia_mod._native_profile_honor_badge(renderer, {}, full_size=False) == ("missing", None)

    payload = {"honor_type": "normal"}
    renderer.profile_honor_requests = {"profile": payload, "duplicate": payload}
    monkeypatch.setattr(skia_mod, "_lower_native_honor_request", lambda value, request: ("unrenderable", None))
    assert skia_mod._native_profile_honor_badge(renderer, {}, full_size=False) == ("missing", None)

    monkeypatch.setattr(skia_mod, "_lower_native_honor_request", lambda value, request: ("hybrid", None))
    assert skia_mod._native_profile_honor_badge(renderer, {}, full_size=False) == ("hybrid", None)

    badge_builder = skia_mod._new_builder(180, 80)
    badge_builder.rect((0, 0), (180, 80), fill=(20, 40, 80, 255))
    badge = _native_subtree_from_builder(badge_builder)
    monkeypatch.setattr(skia_mod, "_lower_native_honor_request", lambda value, request: ("ready", badge))
    assert skia_mod._native_profile_honor_badge(renderer, {}, full_size=False) == ("ready", badge)


def test_native_honor_deck_declines_atomically_when_an_expected_slot_is_missing(monkeypatch):
    badge = skia_mod._new_builder(180, 80)
    badge.rect((0, 0), (180, 80), fill=(20, 40, 80, 255))
    statuses = {1: ("ready", _native_subtree_from_builder(badge)), 2: ("missing", None)}
    monkeypatch.setattr(
        skia_mod,
        "_native_profile_honor_badge",
        lambda renderer, row, *, full_size: statuses[int(row["seq"])],
    )

    class _Renderer:
        def __init__(self):
            self.profile_context = {"userProfileHonors": [{"seq": 1}, {"seq": 2}]}

        def image_resource_for(self, kind, item):
            return {"fileName": "HonorDeck"}

        def center_rect(self, parent_size, center, size):
            return PNGRenderer.center_rect(self, parent_size, center, size)

    builder = skia_mod._new_builder(320, 180)
    scene = skia_mod._SceneAssembler(builder, (320, 180), 8 * 1024 * 1024)
    content = NativeContent(1, "general", {}, {"visible": True})

    assert skia_mod._emit_native_honor_deck(_Renderer(), content, scene) is None
    assert builder.build()["root"]["children"] == []


def test_native_honor_deck_declines_wrong_badge_size_before_mutating_scene(monkeypatch):
    badge = skia_mod._new_builder(10, 10)
    badge.rect((0, 0), (10, 10), fill=(20, 40, 80, 255))
    monkeypatch.setattr(
        skia_mod,
        "_native_profile_honor_badge",
        lambda renderer, row, *, full_size: ("ready", _native_subtree_from_builder(badge)),
    )

    class _Renderer:
        def __init__(self):
            self.profile_context = {"userProfileHonors": [{"seq": 1}]}

        @staticmethod
        def image_resource_for(kind, item):
            return {"fileName": "HonorDeck"}

        def center_rect(self, parent_size, center, size):
            return PNGRenderer.center_rect(self, parent_size, center, size)

    builder = skia_mod._new_builder(320, 180)
    scene = skia_mod._SceneAssembler(builder, (320, 180), 1024)
    content = NativeContent(1, "general", {}, {"visible": True})

    assert skia_mod._emit_native_honor_deck(_Renderer(), content, scene) is None
    assert builder.build()["root"]["children"] == []


def test_native_honor_deck_emits_all_expected_slots_only_after_preflight(monkeypatch):
    def fake_badge(renderer, row, *, full_size):
        width = 380 if full_size else 180
        badge = skia_mod._new_builder(width, 80)
        badge.rect((0, 0), (width, 80), fill=(20 * int(row["seq"]), 40, 80, 255))
        return "ready", _native_subtree_from_builder(badge)

    monkeypatch.setattr(skia_mod, "_native_profile_honor_badge", fake_badge)

    class _Renderer:
        rotation_sign = -1
        position_scale_x = 1.1
        position_scale_y = 1.2

        def __init__(self):
            self.profile_context = {"userProfileHonors": [{"seq": 1}, {"seq": 2}, {"seq": 3}]}

        def image_resource_for(self, kind, item):
            return {"fileName": "HonorDeck"}

        def center_rect(self, parent_size, center, size):
            return PNGRenderer.center_rect(self, parent_size, center, size)

        def unity_ui_sprite_path(self, name):
            return None

        def unity_point(self, position):
            return 160.0, 90.0

    builder = skia_mod._new_builder(320, 180)
    scene = skia_mod._SceneAssembler(builder, (320, 180), 8 * 1024 * 1024)
    content = NativeContent(
        1,
        "general",
        {},
        {
            "visible": True,
            "position": {"x": 0, "y": 0},
            "scale": {"x": 1, "y": 1},
            "rotation": {"z": 0, "w": 1},
        },
    )

    assert skia_mod._emit_native_honor_deck(_Renderer(), content, scene) == "native"
    outer = builder.build()["root"]["children"][0]
    assert outer["type"] == "UnitySubscene"
    assert len([node for node in outer["children"] if node["type"] == "UnitySubscene"]) == 3


def test_native_honor_deck_declines_background_outside_asset_root(monkeypatch):
    def fake_badge(renderer, row, *, full_size):
        width = 380 if full_size else 180
        badge = skia_mod._new_builder(width, 80)
        badge.rect((0, 0), (width, 80), fill=(20, 40, 80, 255))
        return "ready", _native_subtree_from_builder(badge)

    monkeypatch.setattr(skia_mod, "_native_profile_honor_badge", fake_badge)

    class _Renderer:
        def __init__(self):
            self.profile_context = {"userProfileHonors": [{"seq": 1}]}

        @staticmethod
        def image_resource_for(kind, item):
            return {"fileName": "HonorDeck"}

        def center_rect(self, parent_size, center, size):
            return PNGRenderer.center_rect(self, parent_size, center, size)

        @staticmethod
        def unity_ui_sprite_path(name):
            return Path("/outside-assets/honor-panel.png")

    builder = skia_mod._new_builder(320, 180)
    scene = skia_mod._SceneAssembler(builder, (320, 180), 1024)
    content = NativeContent(1, "general", {}, {"visible": True})

    assert skia_mod._emit_native_honor_deck(_Renderer(), content, scene) is None
    assert builder.build()["root"]["children"] == []


def test_native_honor_deck_declines_missing_plan_and_invalid_transform(monkeypatch):
    class _Renderer:
        rotation_sign = -1
        position_scale_x = 1.0
        position_scale_y = 1.0

        def __init__(self):
            self.profile_context = {"userProfileHonors": [{"seq": 1}]}

        @staticmethod
        def image_resource_for(kind, item):
            return {"fileName": "HonorDeck"}

        def center_rect(self, parent_size, center, size):
            return PNGRenderer.center_rect(self, parent_size, center, size)

        @staticmethod
        def unity_ui_sprite_path(name):
            return None

    renderer = _Renderer()
    builder = skia_mod._new_builder(320, 180)
    scene = skia_mod._SceneAssembler(builder, (320, 180), 1024)
    content = NativeContent(1, "general", {}, {"visible": True})
    real_build_plan = skia_mod.build_honor_deck_plan

    monkeypatch.setattr(skia_mod, "build_honor_deck_plan", lambda rows: None)
    assert skia_mod._emit_native_honor_deck(renderer, content, scene) is None

    monkeypatch.setattr(skia_mod, "build_honor_deck_plan", real_build_plan)

    def fake_badge(renderer, row, *, full_size):
        width = 380 if full_size else 180
        badge_builder = skia_mod._new_builder(width, 80)
        badge_builder.rect((0, 0), (width, 80), fill=(20, 40, 80, 255))
        return "ready", _native_subtree_from_builder(badge_builder)

    monkeypatch.setattr(skia_mod, "_native_profile_honor_badge", fake_badge)
    content.object_data["scale"] = {"x": math.nan, "y": 1}
    assert skia_mod._emit_native_honor_deck(renderer, content, scene) is None
    assert builder.build()["root"]["children"] == []


def test_native_card_member_replays_asset_backed_display_list_without_mem(tmp_path, monkeypatch):
    art_path = tmp_path / "card.png"
    Image.new("RGBA", (20, 12), (20, 80, 160, 255)).save(art_path)
    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", tmp_path)
    display_list = CardDisplayList(
        "full",
        (20, 10),
        (CardCoverArtOp(art_path, (20.0, 10.0), blend="src"),),
    )

    class _Renderer:
        rotation_sign = -1
        position_scale_x = 1.25
        position_scale_y = 1.25

        def build_card_member_display_list(self, item):
            return display_list

        def general_font_path(self):
            return None

        def unity_point(self, position):
            return 50.0, 40.0

    builder = skia_mod._new_builder(100, 80)
    scene = skia_mod._SceneAssembler(builder, (100, 80), 8 * 1024 * 1024)
    content = NativeContent(
        1,
        "card_member",
        {},
        {
            "visible": True,
            "position": {"x": 0, "y": 0},
            "scale": {"x": 1, "y": 1},
            "rotation": {"z": 0, "w": 1},
        },
    )

    assert skia_mod._emit_native_card_member(_Renderer(), content, scene)
    assert scene.mem_images == {}
    outer = builder.build()["root"]["children"][0]
    assert outer["type"] == "UnitySubscene"
    assert outer["size"] == [20, 10]
    assert outer["children"][0]["type"] == "Image"
    assert outer["children"][0]["path"] == "card.png"
    assert outer["children"][0]["fit"] == "cover"
    assert outer["children"][0]["blend"] == "src"


def test_native_card_member_declines_missing_required_art_without_mutating_scene(tmp_path, monkeypatch):
    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", tmp_path)
    display_list = CardDisplayList(
        "full",
        (20, 10),
        (CardCoverArtOp(tmp_path / "missing.png", (20.0, 10.0), blend="src"),),
    )

    class _Renderer:
        def build_card_member_display_list(self, item):
            return display_list

        def general_font_path(self):
            return None

    builder = skia_mod._new_builder(100, 80)
    scene = skia_mod._SceneAssembler(builder, (100, 80), 8 * 1024 * 1024)
    content = NativeContent(1, "card_member", {}, {"visible": True})

    assert not skia_mod._emit_native_card_member(_Renderer(), content, scene)
    assert builder.build()["root"]["children"] == []


@pytest.mark.skipif(
    _native is None or getattr(_native, "IR_CAPABILITY", 0) < REQUIRED_NATIVE_IR_CAPABILITY,
    reason="UnitySubscene-capable native renderer is required",
)
@pytest.mark.parametrize("honor_type", ["normal", "birthday"])
def test_synthetic_honor_full_card_native_parity_with_rotation_and_two_stage_scale(
    honor_type,
    tmp_path,
    monkeypatch,
):
    from src.sekai.honor.drawer import compose_full_honor_image_from_loaded_assets
    from src.sekai.honor.model import HonorRequest
    import src.sekai.skia_renderer.canvas as canvas_mod

    base_path = tmp_path / f"{honor_type}_base.png"
    frame_path = tmp_path / f"{honor_type}_frame.png"
    degree_path = tmp_path / "degree.png"
    base = Image.new("RGBA", (101, 43), (0, 0, 0, 0))
    base_draw = ImageDraw.Draw(base)
    base_draw.rounded_rectangle((1, 1, 99, 41), radius=8, fill=(24, 91, 174, 245))
    base_draw.rectangle((13, 8, 75, 33), fill=(231, 188, 52, 210))
    base_draw.ellipse((70, 3, 97, 39), fill=(188, 43, 112, 190))
    base.save(base_path)
    frame = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(frame).rounded_rectangle((1, 1, 99, 41), radius=8, outline=(250, 250, 255, 205), width=3)
    frame.save(frame_path)
    degree = Image.new("RGBA", (9, 9), (255, 245, 80, 210))
    degree.save(degree_path)

    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(canvas_mod, "ASSETS_BASE_DIR", tmp_path)
    request_payload = {
        "honor_type": honor_type,
        "honor_level": 2,
        "is_main_honor": False,
        "honor_img_path": base_path.as_posix(),
        "frame_img_path": frame_path.as_posix(),
    }
    if honor_type == "birthday":
        request_payload["frame_degree_level_img_path"] = degree_path.as_posix()
    honor_request = HonorRequest.model_validate(request_payload)
    object_data = {
        "visible": True,
        "position": {"x": -96.25, "y": 37.5},
        "scale": {"x": 0.83, "y": 1.17},
        "rotation": {
            "z": math.sin(math.radians(6.5)),
            "w": math.cos(math.radians(6.5)),
        },
    }
    honor_content = NativeContent(
        layer=1,
        kind="honor",
        item={"id": 123, "fullSize": False},
        object_data=object_data,
    )

    class _Renderer:
        rotation_sign = 1
        position_scale_x = 1.113
        position_scale_y = 1.087
        origin_x = PROFILE_RENDER_VIEW_W / 2.0
        origin_y = PROFILE_RENDER_VIEW_H / 2.0
        tmp_decorative_alpha_harden = 1.0

        def __init__(self):
            self.honor_requests = {"123:2:sub": request_payload}

        def native_card_ref(self, card):
            return {}

        def build_native_contents(self, card):
            return [honor_content]

        def user_honor_level_for(self, honor_id):
            return 2

        def honor_slot_key(self, honor_id, level, full_size):
            return f"{honor_id}:{level}:{'main' if full_size else 'sub'}"

        def resolve_request_asset_path(self, raw_path):
            path = Path(raw_path)
            return path.resolve() if path.is_file() else None

        def unity_point(self, position):
            return (
                self.origin_x + float(position.get("x", 0)) * self.position_scale_x,
                self.origin_y - float(position.get("y", 0)) * self.position_scale_y,
            )

        def record_native_audit(self, *args):
            return None

        def render_content_for_card(self, content):  # pragma: no cover - must not run
            raise AssertionError("eligible honor must not enter the Pillow renderer")

    renderer = _Renderer()
    ir_json, mem_images, report = _build_scene(renderer, {})
    assert report.complete
    assert report.native_elements == 1
    assert mem_images == {}
    native_result = _native.render_scene(ir_json, mem_images)
    native_payload = skia_mod.payload_from_native(native_result)
    native_image = Image.open(BytesIO(native_payload.image_bytes)).convert("RGBA")

    pillow_badge = compose_full_honor_image_from_loaded_assets(
        honor_request,
        {
            "honor_img": base,
            "rank_img": None,
            "frame_img": frame,
            "frame_degree_level_img": degree if honor_type == "birthday" else None,
            "scroll_img": None,
            "lv_img": None,
            "lv6_img": None,
        },
    )
    assert pillow_badge is not None
    transformer = object.__new__(PNGRenderer)
    transformer.position_scale_x = renderer.position_scale_x
    transformer.position_scale_y = renderer.position_scale_y
    transformer.origin_x = renderer.origin_x
    transformer.origin_y = renderer.origin_y
    transformer.rotation_sign = renderer.rotation_sign
    transformer.canvas_w = int(PROFILE_RENDER_VIEW_W)
    transformer.canvas_h = int(PROFILE_RENDER_VIEW_H)
    transformer.clip_canvas_transform = True
    transformer.max_layer_pixels = 8 * 1024 * 1024
    transformer.premultiply_alpha_transforms = False
    prepared = transformer.prepare_transformed_layer(
        (pillow_badge, (pillow_badge.width / 2, pillow_badge.height / 2)),
        object_data,
        "honor",
        False,
    )
    assert prepared is not None
    pillow_image = Image.new(
        "RGBA",
        (int(PROFILE_RENDER_VIEW_W), int(PROFILE_RENDER_VIEW_H)),
        (255, 255, 255, 255),
    )
    pillow_image.alpha_composite(prepared.image, prepared.xy)

    diff = ImageChops.difference(pillow_image, native_image).convert("RGB")
    pillow_bbox = (
        ImageChops.difference(
            pillow_image,
            Image.new("RGBA", pillow_image.size, (255, 255, 255, 255)),
        )
        .convert("RGB")
        .getbbox()
    )
    native_bbox = (
        ImageChops.difference(
            native_image,
            Image.new("RGBA", native_image.size, (255, 255, 255, 255)),
        )
        .convert("RGB")
        .getbbox()
    )
    assert pillow_bbox is not None
    assert native_bbox is not None
    bbox = (
        min(pillow_bbox[0], native_bbox[0]),
        min(pillow_bbox[1], native_bbox[1]),
        max(pillow_bbox[2], native_bbox[2]),
        max(pillow_bbox[3], native_bbox[3]),
    )
    local = diff.crop(bbox)
    histogram = local.histogram()
    channel_pixels = local.width * local.height * 3
    local_mean = sum(value * histogram[channel * 256 + value] for channel in range(3) for value in range(256))
    local_mean /= channel_pixels
    threshold = math.ceil(channel_pixels * 0.99)
    seen = 0
    local_p99 = 0
    for value in range(256):
        seen += sum(histogram[channel * 256 + value] for channel in range(3))
        if seen >= threshold:
            local_p99 = value
            break

    assert local_mean <= 3.0, (honor_type, local_mean, local_p99)
    assert local_p99 <= 25, (honor_type, local_mean, local_p99)


@pytest.mark.skipif(
    _native is None
    or getattr(_native, "IR_CAPABILITY", 0) < REQUIRED_NATIVE_IR_CAPABILITY
    or getattr(_native, "ASSET_INFO_CAPABILITY", 0) < 1,
    reason="asset-backed bonds honor renderer is required",
)
@pytest.mark.parametrize("is_main_honor", [False, True], ids=["sub", "main"])
def test_bonds_honor_category_only_fixture_is_native_pixel_pure(is_main_honor, tmp_path, monkeypatch):
    from src.sekai.honor.drawer import compose_full_honor_image_from_loaded_assets
    from src.sekai.honor.model import HonorRequest
    import src.sekai.skia_renderer.canvas as canvas_mod

    badge_size = (380, 80) if is_main_honor else (180, 80)
    paths = {
        name: tmp_path / f"{name}.png" for name in ("left", "right", "one", "two", "mask", "frame", "word", "lv", "lv6")
    }

    left = Image.new("RGBA", badge_size, (0, 0, 0, 0))
    ImageDraw.Draw(left).rounded_rectangle(
        (0, 0, badge_size[0] - 1, badge_size[1] - 1),
        radius=12,
        fill=(34, 91, 178, 239),
    )
    right = Image.new("RGBA", badge_size, (0, 0, 0, 0))
    ImageDraw.Draw(right).polygon(
        ((0, 0), (badge_size[0] - 1, 0), (badge_size[0] - 20, badge_size[1] - 1), (18, badge_size[1] - 1)),
        fill=(207, 69, 136, 219),
    )
    one = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(one).ellipse((4, 4, 95, 95), fill=(246, 197, 65, 232), outline=(255, 255, 255, 255), width=4)
    two = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(two).rounded_rectangle(
        (6, 6, 93, 93),
        radius=18,
        fill=(76, 211, 172, 221),
        outline=(255, 255, 255, 255),
        width=4,
    )
    mask = Image.new("RGBA", badge_size, (0, 0, 0, 0))
    ImageDraw.Draw(mask).rounded_rectangle(
        (3, 3, badge_size[0] - 4, badge_size[1] - 4),
        radius=13,
        fill=(255, 255, 255, 224),
    )
    frame = Image.new("RGBA", badge_size, (0, 0, 0, 0))
    ImageDraw.Draw(frame).rounded_rectangle(
        (1, 1, badge_size[0] - 2, badge_size[1] - 2),
        radius=12,
        outline=(250, 250, 255, 211),
        width=3,
    )
    word = Image.new("RGBA", (92, 24), (0, 0, 0, 0))
    ImageDraw.Draw(word).rounded_rectangle((0, 0, 91, 23), radius=5, fill=(255, 238, 145, 203))
    lv = Image.new("RGBA", (22, 22), (255, 213, 66, 221))
    lv6 = Image.new("RGBA", (22, 22), (238, 89, 142, 221))
    images = {
        "bonds_bg": left,
        "bonds_bg2": right,
        "chara_icon_1": one,
        "chara_icon_2": two,
        "mask_img": mask,
        "frame_img": frame,
        "word_img": word if is_main_honor else None,
        "lv_img": lv,
        "lv6_img": lv6,
    }
    for key, image in {
        "left": left,
        "right": right,
        "one": one,
        "two": two,
        "mask": mask,
        "frame": frame,
        "word": word,
        "lv": lv,
        "lv6": lv6,
    }.items():
        image.save(paths[key])

    monkeypatch.setattr(skia_mod, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(canvas_mod, "ASSETS_BASE_DIR", tmp_path)
    request_payload = {
        "honor_type": "bonds",
        "honor_level": 7,
        "honor_rarity": "highest",
        "is_main_honor": is_main_honor,
        "bonds_bg_path": paths["left"].as_posix(),
        "bonds_bg_path2": paths["right"].as_posix(),
        "chara_icon_path": paths["one"].as_posix(),
        "chara_icon_path2": paths["two"].as_posix(),
        "mask_img_path": paths["mask"].as_posix(),
        "frame_img_path": paths["frame"].as_posix(),
        "lv_img_path": paths["lv"].as_posix(),
        "lv6_img_path": paths["lv6"].as_posix(),
    }
    if is_main_honor:
        request_payload["word_img_path"] = paths["word"].as_posix()
    honor_request = HonorRequest.model_validate(request_payload)
    angle = 9.0
    object_data = {
        "visible": True,
        "position": {"x": 71.5, "y": -42.25},
        "scale": {"x": 0.86, "y": 1.09},
        "rotation": {"z": math.sin(math.radians(angle) / 2.0), "w": math.cos(math.radians(angle) / 2.0)},
    }
    content = NativeContent(
        layer=1,
        kind="bonds_honor",
        item={"id": 456, "fullSize": is_main_honor, "wordId": 0, "inverse": False},
        object_data=object_data,
    )
    slot_key = f"456:7:{'main' if is_main_honor else 'sub'}:0:normal"

    class _Renderer:
        rotation_sign = 1
        position_scale_x = 1.113
        position_scale_y = 1.087
        origin_x = PROFILE_RENDER_VIEW_W / 2.0
        origin_y = PROFILE_RENDER_VIEW_H / 2.0

        def __init__(self):
            self.bonds_honor_requests = {slot_key: request_payload}

        def general_font_path(self):
            return None

        def native_card_ref(self, card):
            return {}

        def build_native_contents(self, card):
            return [content]

        def user_bonds_honor_level_for(self, honor_id):
            return 7

        def bonds_honor_slot_key(
            self,
            honor_id,
            level,
            full_size,
            word_id,
            inverse,
            use_unit_virtual_singer=False,
        ):
            assert not use_unit_virtual_singer
            return f"{honor_id}:{level}:{'main' if full_size else 'sub'}:{word_id}:{'reverse' if inverse else 'normal'}"

        def resolve_request_asset_path(self, raw_path):
            path = Path(raw_path)
            return path.resolve() if path.is_file() else None

        def unity_point(self, position):
            return (
                self.origin_x + float(position.get("x", 0.0)) * self.position_scale_x,
                self.origin_y - float(position.get("y", 0.0)) * self.position_scale_y,
            )

        def record_native_audit(self, *args):
            return None

        def render_content_for_card(self, content):  # pragma: no cover - must not run
            raise AssertionError("eligible bonds honor must not enter the Pillow renderer")

    renderer = _Renderer()
    token = begin_pillow_touch_scope()
    try:
        ir_json, mem_images, report = _build_scene(renderer, {})
        native_result = _native.render_scene(ir_json, mem_images)
        pillow_touches = take_pillow_touch_snapshot()
    finally:
        end_pillow_touch_scope(token)

    assert report.complete
    assert report.classifications_by_kind == {"bonds_honor": {"native": 1}}
    assert mem_images == {}
    assert b"mem:" not in ir_json
    assert pillow_touches.counts == {}
    scene = json.loads(ir_json)
    assert any(node.get("blend") == "paste_lerp" for node in _walk_ir_nodes(scene["root"]["children"]))

    native_payload = skia_mod.payload_from_native(native_result)
    native_image = Image.open(BytesIO(native_payload.image_bytes)).convert("RGBA")
    pillow_badge = compose_full_honor_image_from_loaded_assets(honor_request, images)
    assert pillow_badge is not None
    transformer = object.__new__(PNGRenderer)
    transformer.position_scale_x = renderer.position_scale_x
    transformer.position_scale_y = renderer.position_scale_y
    transformer.origin_x = renderer.origin_x
    transformer.origin_y = renderer.origin_y
    transformer.rotation_sign = renderer.rotation_sign
    transformer.canvas_w = int(PROFILE_RENDER_VIEW_W)
    transformer.canvas_h = int(PROFILE_RENDER_VIEW_H)
    transformer.clip_canvas_transform = True
    transformer.max_layer_pixels = 8 * 1024 * 1024
    transformer.premultiply_alpha_transforms = False
    prepared = transformer.prepare_transformed_layer(
        (pillow_badge, (pillow_badge.width / 2.0, pillow_badge.height / 2.0)),
        object_data,
        "bonds_honor",
        False,
    )
    assert prepared is not None
    pillow_image = Image.new(
        "RGBA",
        (int(PROFILE_RENDER_VIEW_W), int(PROFILE_RENDER_VIEW_H)),
        (255, 255, 255, 255),
    )
    pillow_image.alpha_composite(prepared.image, prepared.xy)
    mean, p99 = _local_rgb_diff_metrics(pillow_image, native_image)

    assert mean <= 3.5, (is_main_honor, mean, p99)
    assert p99 <= 35, (is_main_honor, mean, p99)


# ------------------------------- the route contract -------------------------------


def test_route_serves_the_skia_payload_without_composing(monkeypatch):
    payload = EncodedImagePayload(
        image_bytes=_png_bytes(),
        media_type="image/png",
        filename="image.png",
        image_width=8,
        image_height=8,
        image_mode="RGBA",
        encode_elapsed=0.0,
    )

    async def fake_attempt(request):
        return skia_mod.CustomProfileSkiaAttempt(payload, OUTCOME_SKIA)

    async def _must_not_compose(request):  # pragma: no cover - must not run
        raise AssertionError("compose must not run when Skia produced a payload")

    monkeypatch.setattr(route_mod, "try_render_custom_profile_card_attempt", fake_attempt)
    monkeypatch.setattr(route_mod, "compose_custom_profile_card_image", _must_not_compose)

    response = asyncio.run(route_mod.custom_profile_card(_request()))
    assert response.media_type == "image/png"
    assert response.body == payload.image_bytes


def test_route_falls_back_to_pillow_compose(monkeypatch):
    seen_backend = None

    async def fake_attempt(request):
        return skia_mod.CustomProfileSkiaAttempt(None, OUTCOME_FALLBACK)

    async def fake_compose(request):
        return Image.new("RGBA", (8, 8), (255, 0, 0, 128))

    original_image_to_response = route_mod.image_to_response

    async def observing_image_to_response(*args, **kwargs):
        nonlocal seen_backend
        seen_backend = current_render_backend()
        return await original_image_to_response(*args, **kwargs)

    monkeypatch.setattr(route_mod, "try_render_custom_profile_card_attempt", fake_attempt)
    monkeypatch.setattr(route_mod, "compose_custom_profile_card_image", fake_compose)
    monkeypatch.setattr(route_mod, "image_to_response", observing_image_to_response)

    response = asyncio.run(route_mod.custom_profile_card(_request()))
    # The route pins PNG regardless of the global EXPORT_IMAGE_FORMAT (the card has transparency).
    assert response.media_type == "image/png"
    assert Image.open(BytesIO(response.body)).format == "PNG"
    assert _endpoint_stats()["fallback"] == 1
    assert seen_backend == "skia_fallback"


def test_route_records_skia_error_when_pillow_recovers(monkeypatch, caplog):
    persisted = []

    async def fake_attempt(request):
        return skia_mod.CustomProfileSkiaAttempt(
            None,
            OUTCOME_ERROR,
            error_stage="native_render",
            error_type="RuntimeError",
        )

    async def fake_compose(request):
        return Image.new("RGBA", (8, 8), (255, 0, 0, 128))

    monkeypatch.setattr(route_mod, "try_render_custom_profile_card_attempt", fake_attempt)
    monkeypatch.setattr(route_mod, "compose_custom_profile_card_image", fake_compose)
    monkeypatch.setattr(
        skia_mod,
        "persist_custom_profile_diagnostic",
        lambda **kwargs: persisted.append(kwargs) or True,
    )

    with caplog.at_level(logging.ERROR, logger="custom_profile.draw.perf"):
        response = asyncio.run(route_mod.custom_profile_card(_request()))
    assert response.status_code == 200
    stats = _endpoint_stats()
    assert stats["error"] == 1
    assert stats["errors_by_stage"] == {"native_render": 1}
    errors = [record for record in caplog.records if record.name == "custom_profile.draw.perf"]
    assert len(errors) == 1
    assert errors[0].levelno == logging.ERROR
    assert "committed_error stage=native_render error_type=RuntimeError" in errors[0].message
    assert len(persisted) == 1
    assert persisted[0]["final_http_status"] == 200


def test_route_records_final_5xx_class_for_committed_skia_error(monkeypatch):
    persisted = []

    async def fake_attempt(request):
        return skia_mod.CustomProfileSkiaAttempt(
            None,
            OUTCOME_ERROR,
            error_stage="native_render",
            error_type="RuntimeError",
        )

    async def fake_compose(request):
        raise RuntimeError("fallback failed")

    monkeypatch.setattr(route_mod, "try_render_custom_profile_card_attempt", fake_attempt)
    monkeypatch.setattr(route_mod, "compose_custom_profile_card_image", fake_compose)
    monkeypatch.setattr(
        skia_mod,
        "persist_custom_profile_diagnostic",
        lambda **kwargs: persisted.append(kwargs) or True,
    )

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(route_mod.custom_profile_card(_request()))
    assert excinfo.value.status_code == 500
    assert len(persisted) == 1
    assert persisted[0]["final_http_status"] == 500


def test_route_preserves_the_value_error_400(monkeypatch, caplog):
    """A rejected request reaches the canonical 400 without poisoning the native gate."""

    async def fake_attempt(request):
        return skia_mod.CustomProfileSkiaAttempt(
            None,
            OUTCOME_ERROR,
            error_stage="native_render",
            error_type="ValueError",
        )

    async def fake_compose(request):
        raise ValueError("bad card")

    monkeypatch.setattr(route_mod, "try_render_custom_profile_card_attempt", fake_attempt)
    monkeypatch.setattr(route_mod, "compose_custom_profile_card_image", fake_compose)

    with caplog.at_level(logging.WARNING, logger="custom_profile.draw.perf"):
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(route_mod.custom_profile_card(_request()))
    assert excinfo.value.status_code == 400
    assert "bad card" in excinfo.value.detail
    assert get_render_stats()["endpoints"] == {}
    records = [record for record in caplog.records if record.name == "custom_profile.draw.perf"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert "rejected_request stage=native_render error_type=ValueError" in records[0].message


@pytest.mark.parametrize("scale", [1.0e9, 10**400])
def test_route_rejects_unbounded_scale_before_native_or_fallback(monkeypatch, scale):
    request = CustomProfileCardRenderRequest(
        card={
            "customProfileCard": {
                "shapes": [
                    {
                        "id": 1,
                        "objectData": {
                            "visible": True,
                            "layer": 1,
                            "position": {"x": 0, "y": 0},
                            "scale": {"x": scale, "y": scale},
                            "rotation": {"z": 0, "w": 1},
                        },
                    }
                ]
            }
        }
    )

    async def _must_not_render(request):  # pragma: no cover - must not run
        raise AssertionError("validation must run before either renderer")

    monkeypatch.setattr(route_mod, "try_render_custom_profile_card_attempt", _must_not_render)
    monkeypatch.setattr(route_mod, "compose_custom_profile_card_image", _must_not_render)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(route_mod.custom_profile_card(request))
    assert excinfo.value.status_code == 400
    assert "scale" in excinfo.value.detail


# ------------------------------- native end-to-end -------------------------------


@pytest.mark.skipif(
    _native is None or getattr(_native, "IR_CAPABILITY", 0) < REQUIRED_NATIVE_IR_CAPABILITY,
    reason=(
        "haruki_skia_renderer not built at the production REQUIRED_NATIVE_IR_CAPABILITY "
        "(an older wheel would pass this gate and then be rejected by load_native_renderer)"
    ),
)
@pytest.mark.skipif(not PAYLOAD_FILE.is_file(), reason="out/parity-payloads fixture not present")
def test_native_end_to_end_renders_the_real_payload(monkeypatch):
    from src.settings import settings

    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)
    request = CustomProfileCardRenderRequest.model_validate(json.loads(PAYLOAD_FILE.read_text(encoding="utf-8")))

    token = begin_pillow_touch_scope()
    try:
        payload = asyncio.run(try_render_custom_profile_card_payload(request))
        pillow_touches = take_pillow_touch_snapshot()
    finally:
        end_pillow_touch_scope(token)
    assert payload is not None, "the real parity payload must render via Skia, not fall back"
    assert payload.media_type == "image/png"
    assert payload.backend == "skia"

    image = Image.open(BytesIO(payload.image_bytes))
    assert image.size == (2048, 909)  # PROFILE_RENDER_VIEW_W x PROFILE_RENDER_VIEW_H
    assert image.mode == "RGBA"

    stats = _endpoint_stats()
    assert stats["skia"] == 1
    assert stats["total"] == 1
    assert stats["native_pure"] == 1
    assert stats["native_hybrid"] == 0
    assert stats["pillow_touch_reasons"] == {}
    assert pillow_touches.counts == {}
    assert payload.native_metrics["custom_profile_hybrid_elements"] == 0
    assert payload.native_metrics["custom_profile_mem_images"] == 0
    assert payload.native_metrics["custom_profile_mem_bytes"] == 0


@pytest.mark.skipif(
    _native is None or getattr(_native, "IR_CAPABILITY", 0) < REQUIRED_NATIVE_IR_CAPABILITY,
    reason="current native renderer is required",
)
@pytest.mark.skipif(not PAYLOAD_FILE.is_file(), reason="out/parity-payloads fixture not present")
def test_native_sdf_shape_matches_pillow_without_mem_transport(monkeypatch):
    from src.settings import settings

    raw = json.loads(PAYLOAD_FILE.read_text(encoding="utf-8"))
    layout = raw["card"]["customProfileCard"]
    for value in layout.values():
        if isinstance(value, list):
            value.clear()
    layout["shapes"] = [
        {
            "id": 1,
            "colorId": 3,
            "outlineColorId": 4,
            "alpha": 0.85,
            "outlineAlpha": 0.7,
            "outlineSize": 0.2,
            "objectData": {
                "position": {"x": 0, "y": 0, "z": 0},
                "scale": {"x": 1.5, "y": 0.75, "z": 1},
                "rotation": {"x": 0, "y": 0, "z": 0, "w": 1},
                "layer": 1,
                "lock": False,
                "visible": True,
            },
        }
    ]
    request = CustomProfileCardRenderRequest.model_validate(raw)
    captured: dict[str, object] = {}

    class _NativeProxy:
        def render_scene(self, ir_json, mem_images):
            captured["ir"] = json.loads(ir_json)
            captured["mem_images"] = mem_images
            return _native.render_scene(ir_json, mem_images)

    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)
    monkeypatch.setattr(skia_mod, "load_native_renderer", _NativeProxy)

    pillow = asyncio.run(compose_custom_profile_card_image(request))
    payload = asyncio.run(try_render_custom_profile_card_payload(request))
    assert payload is not None
    native = Image.open(BytesIO(payload.image_bytes)).convert("RGBA")
    diff = ImageChops.difference(pillow, native)
    histogram = diff.convert("RGB").histogram()
    channel_pixels = pillow.width * pillow.height * 3
    mean = sum(value * histogram[channel * 256 + value] for channel in range(3) for value in range(256))
    mean /= channel_pixels
    white = Image.new("RGBA", pillow.size, (255, 255, 255, 255))
    pillow_bbox = ImageChops.difference(pillow, white).convert("RGB").getbbox()
    native_bbox = ImageChops.difference(native, white).convert("RGB").getbbox()
    assert pillow_bbox is not None
    assert native_bbox is not None
    content_bbox = (
        min(pillow_bbox[0], native_bbox[0]),
        min(pillow_bbox[1], native_bbox[1]),
        max(pillow_bbox[2], native_bbox[2]),
        max(pillow_bbox[3], native_bbox[3]),
    )
    local_histogram = diff.crop(content_bbox).convert("RGB").histogram()
    local_pixels = (content_bbox[2] - content_bbox[0]) * (content_bbox[3] - content_bbox[1]) * 3
    local_mean = sum(value * local_histogram[channel * 256 + value] for channel in range(3) for value in range(256))
    local_mean /= local_pixels
    threshold = math.ceil(local_pixels * 0.99)
    seen = 0
    local_p99 = 0
    for value in range(256):
        seen += sum(local_histogram[channel * 256 + value] for channel in range(3))
        if seen >= threshold:
            local_p99 = value
            break

    assert captured["mem_images"] == {}
    assert any(node["type"] == "SdfShape" for node in captured["ir"]["root"]["children"])
    assert mean <= 0.5
    assert local_mean <= 2.0, local_mean
    assert local_p99 <= 25, local_p99
