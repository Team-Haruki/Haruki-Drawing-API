"""TMP end-to-end coverage with generated metadata and a bundled public font."""

from io import BytesIO
import math

import numpy as np
from PIL import Image, ImageChops
import pytest

from src.core.pillow_telemetry import begin_pillow_touch_scope, end_pillow_touch_scope, take_pillow_touch_snapshot
from src.sekai.profile.custom_profile.renderer import PROFILE_RENDER_VIEW_H, PROFILE_RENDER_VIEW_W, PNGRenderer
from src.sekai.profile.custom_profile.skia import _build_scene
from src.sekai.skia_renderer.canvas import load_native_renderer
from tests.profile_font_fixture import build_tmp_fixture


@pytest.mark.parametrize("low_budget", [False, True])
@pytest.mark.parametrize("font_id", [1, 2])
@pytest.mark.parametrize(
    ("text", "outline", "angle"),
    [
        ("AVATAR j\n\nLine 2", 0, 0),
        ("Outlined", 0.2, 17),
        ("<color=#ff66bb><rotate=-9>Rich</rotate></color>", 0.2, 0),
        ("<size=18>A</size><space=10>B\n<indent=10%>C</indent>", 0, -12),
    ],
)
def test_synthetic_tmp_scene_is_native_and_preserves_reference_placement(
    tmp_path, text, outline, angle, font_id, low_budget
):
    try:
        native = load_native_renderer()
    except ImportError:
        pytest.skip("native renderer required")
    fixture = build_tmp_fixture(tmp_path)
    width, height = int(PROFILE_RENDER_VIEW_W), int(PROFILE_RENDER_VIEW_H)
    renderer = PNGRenderer(
        masterdata=None,
        assets=tmp_path,
        fonts=tmp_path,
        tmp_font_metadata=fixture.metadata,
        resources=fixture.resources,
        shape_sprite_dir=tmp_path,
        unity_ui_sprite_dir=tmp_path,
        canvas_w=width,
        canvas_h=height,
        origin_x=width / 2,
        origin_y=height / 2,
    )
    half = math.radians(angle) / 2
    card = {
        "seq": 1,
        "customProfileCardId": 1,
        "customProfileCard": {
            "texts": [
                {
                    "fontId": font_id,
                    "colorId": 1,
                    "outlineColorId": 2,
                    "outlineAlpha": 1,
                    "outlineSize": outline,
                    "alpha": 0.8,
                    "size": 28,
                    "text": text,
                    "objectData": {
                        "position": {"x": -180, "y": 96},
                        "scale": {"x": 1.2, "y": 0.8},
                        "rotation": {"z": math.sin(half), "w": math.cos(half)},
                        "layer": 1,
                        "visible": True,
                    },
                }
            ]
        },
    }
    expected = renderer.render_card(card)
    if low_budget:
        renderer.max_layer_pixels = 1024
    token = begin_pillow_touch_scope()
    try:
        if low_budget:
            from src.sekai.profile.custom_profile.limits import RasterSizeLimitError

            with pytest.raises(RasterSizeLimitError, match="would allocate"):
                _build_scene(renderer, card)
            assert take_pillow_touch_snapshot().counts == {}
            return
        scene, memory, report = _build_scene(renderer, card)
        result = native.render_scene(scene, memory)
        assert take_pillow_touch_snapshot().counts == {}
    finally:
        end_pillow_touch_scope(token)
    assert report.visible_elements == 1
    assert report.native_elements == 1
    assert report.missing_elements == 0
    actual = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")
    assert actual.size == expected.size
    white = Image.new("RGB", actual.size, "white")
    bounds = [ImageChops.difference(im.convert("RGB"), white).getbbox() for im in (actual, expected)]
    assert all(bounds)
    assert max(abs(a - b) for a, b in zip(*bounds, strict=True)) <= 2
    diff = np.abs(np.asarray(actual).astype(int) - np.asarray(expected).astype(int))
    assert diff.mean() < 0.25
    assert np.array_equal(np.asarray(actual)[:, :, 3], np.asarray(expected)[:, :, 3])


@pytest.mark.parametrize("kind", ["stamps", "shapes"])
@pytest.mark.parametrize("angle", [0, 17])
def test_synthetic_asset_layers_keep_native_placement_and_alpha(tmp_path, monkeypatch, kind, angle):
    from src.sekai.profile.custom_profile import skia

    try:
        native = load_native_renderer()
    except ImportError:
        pytest.skip("native renderer required")
    fixture = build_tmp_fixture(tmp_path)
    path = tmp_path / "layer.png"
    layer = Image.new("RGBA", (64, 64))
    layer.putdata([(x * 4, y * 4, 128, 255 if 8 < x < 55 and 8 < y < 55 else 0) for y in range(64) for x in range(64)])
    layer.save(path)
    fixture.resources["stampAssets"] = [{"id": 1, "imagePath": str(path)}]
    fixture.resources["customProfileShapeResources"] = [{"id": 1, "imagePath": str(path), "fileName": "fixture"}]
    monkeypatch.setattr(skia, "ASSETS_BASE_DIR", tmp_path)
    width, height = int(PROFILE_RENDER_VIEW_W), int(PROFILE_RENDER_VIEW_H)
    renderer = PNGRenderer(
        masterdata=None,
        assets=tmp_path,
        fonts=tmp_path,
        tmp_font_metadata=fixture.metadata,
        resources=fixture.resources,
        shape_sprite_dir=tmp_path,
        unity_ui_sprite_dir=tmp_path,
        canvas_w=width,
        canvas_h=height,
        origin_x=width / 2,
        origin_y=height / 2,
    )
    half = math.radians(angle) / 2
    card = {
        "seq": 1,
        "customProfileCardId": 1,
        "customProfileCard": {
            kind: [
                {
                    "id": 1,
                    "colorId": 1,
                    "outlineColorId": 2,
                    "alpha": 0.8,
                    "outlineAlpha": 0.5,
                    "outlineSize": 0.2,
                    "objectData": {
                        "position": {"x": 120, "y": 80},
                        "scale": {"x": 1.3, "y": 0.7},
                        "rotation": {"z": math.sin(half), "w": math.cos(half)},
                        "visible": True,
                        "layer": 1,
                    },
                }
            ]
        },
    }
    expected = renderer.render_card(card)
    token = begin_pillow_touch_scope()
    try:
        scene, memory, report = skia._build_scene(renderer, card)
        result = native.render_scene(scene, memory)
        assert take_pillow_touch_snapshot().counts == {}
    finally:
        end_pillow_touch_scope(token)
    assert report.native_elements == 1
    assert report.missing_elements == 0
    actual = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")
    white = Image.new("RGB", actual.size, "white")
    bounds = [ImageChops.difference(im.convert("RGB"), white).getbbox() for im in (actual, expected)]
    assert all(bounds)
    assert max(abs(a - b) for a, b in zip(*bounds, strict=True)) <= 2
    assert np.abs(np.asarray(actual).astype(int) - np.asarray(expected).astype(int)).mean() < 0.25
