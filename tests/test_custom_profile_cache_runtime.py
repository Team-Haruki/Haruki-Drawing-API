from __future__ import annotations

from pathlib import Path

from PIL import Image
import pytest

from src.sekai.profile.custom_profile import renderer as renderer_mod
from src.sekai.profile.custom_profile.renderer import PNGRenderer, PreparedLayer


def _renderer(tmp_path: Path) -> PNGRenderer:
    fonts = tmp_path / "fonts"
    assets = tmp_path / "asset" / "cn-assets" / "startapp" / "custom_profile"
    fonts.mkdir()
    assets.mkdir(parents=True)
    return PNGRenderer(
        masterdata=None,
        assets=assets,
        fonts=fonts,
        resources={},
        tmp_font_metadata=None,
        shape_sprite_dir=None,
        unity_ui_sprite_dir=None,
        profile_context={},
        region="cn",
    )


def _shape(path: Path) -> None:
    image = Image.new("RGBA", (9, 7), (0, 0, 0, 0))
    for y in range(1, 6):
        for x in range(1, 8):
            image.putpixel((x, y), (x * 25, y * 35, 128, 255 if 2 <= x <= 6 else 128))
    image.save(path)


def test_shape_mask_distance_and_shader_caches_cover_all_sources(tmp_path: Path):
    renderer = _renderer(tmp_path)
    path = tmp_path / "shape.png"
    _shape(path)

    renderer.triangle_mode = "sharp"
    sharp = renderer.shape_alpha_mask(path, "triangle")
    assert sharp is renderer.shape_alpha_mask(path, "triangle")
    assert sharp.size == (9, 7)

    renderer.triangle_mode = "sprite"
    sprite = renderer.shape_alpha_mask(path, "triangle")
    assert sprite.getextrema()[1] > 0

    asset_alpha = renderer.shape_alpha_mask(path, "circle")
    assert asset_alpha.getextrema()[1] == 255

    renderer.shape_sdf_source = "alpha"
    alpha_field = renderer.shape_distance_field(path, "circle")
    assert alpha_field is renderer.shape_distance_field(path, "circle")

    renderer.shape_sdf_source = "rgb"
    renderer.triangle_mode = "sharp"
    sharp_field = renderer.shape_distance_field(path, "triangle")
    assert sharp_field.size == (9, 7)
    renderer.triangle_mode = "sprite"
    sprite_field = renderer.shape_distance_field(path, "triangle")
    assert sprite_field.size == (9, 7)
    rgb_field = renderer.shape_distance_field(path, "circle")
    assert rgb_field.size == (9, 7)

    sdf_alpha = renderer.shape_sdf_alpha(path, "circle", 0.5, 0.1)
    assert sdf_alpha is renderer.shape_sdf_alpha(path, "circle", 0.5, 0.1)
    basis = renderer.shape_shader_basis(path, "circle")
    assert basis is renderer.shape_shader_basis(path, "circle")
    assert basis[0].shape == (7, 9)
    assert renderer.shape_shader_arrays(path, "circle", None) is basis
    resized = renderer.shape_shader_arrays(path, "circle", (5, 4))
    assert resized[0].shape == (4, 5)


def test_card_member_composition_handles_contain_crop_and_existing_candidates(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    path = tmp_path / "member.png"
    Image.new("RGBA", (20, 10), (255, 0, 0, 255)).save(path)

    contained = renderer.compose_card_member_image(path, (10, 10), contain=True)
    cropped = renderer.compose_card_member_image(path, (10, 10), contain=False)
    assert contained.size == (10, 10)
    assert cropped.size == (10, 10)
    assert contained.getchannel("A").getbbox() is not None
    assert cropped.getchannel("A").getbbox() is not None

    missing = tmp_path / "missing.png"
    monkeypatch.setattr(renderer, "card_member_image_candidates", lambda _item: [missing, path])
    assert renderer.card_member_image_path({"id": 1}) == path
    monkeypatch.setattr(renderer, "card_member_image_candidates", lambda _item: [missing])
    assert renderer.card_member_image_path({"id": 1}) is None


def test_transform_helpers_cover_premultiplied_scaling_clipping_and_composite(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    renderer.canvas_w = 40
    renderer.canvas_h = 30
    renderer.origin_x = 20
    renderer.origin_y = 15
    renderer.position_scale_x = 1.5
    renderer.position_scale_y = 2.0
    layer = Image.new("RGBA", (8, 6), (255, 0, 0, 200))

    renderer.premultiply_alpha_transforms = True
    resized = renderer.resize_layer_for_transform(layer, (12, 9), Image.Resampling.BICUBIC)
    transformed = renderer.affine_transform_layer(layer, (8, 6), (1, 0, 0, 0, 1, 0), Image.Resampling.BICUBIC)
    assert resized.size == (12, 9)
    assert transformed.size == (8, 6)

    renderer.premultiply_alpha_transforms = False
    assert renderer.resize_layer_for_transform(layer, (4, 3), Image.Resampling.BILINEAR).size == (4, 3)
    assert renderer.affine_transform_layer(layer, (8, 6), (1, 0, 0, 0, 1, 0), Image.Resampling.BICUBIC).size == (8, 6)

    object_data = {
        "scale": {"x": 2.0, "y": 0.5},
        "position": {"x": 2.0, "y": 3.0},
        "rotation": {"z": 0.0},
    }
    normal = renderer.layer_transform_inputs((layer, (4, 3)), object_data, "general")
    assert normal.object_scale == (2.0, 0.5)
    assert normal.anchor == (23.0, 9.0)
    consumed = renderer.layer_transform_inputs((layer, (4, 3), True), object_data, "shape")
    assert consumed.object_scale == (1.0, 1.0)

    renderer.clip_canvas_transform = True
    prepared = renderer.prepare_transformed_layer((layer, (4, 3)), object_data, "general")
    assert prepared is not None
    assert prepared.image.size != layer.size

    renderer.clip_canvas_transform = False
    full = renderer.prepare_transformed_layer((layer, (4, 3)), object_data, "general")
    assert full is not None

    canvas = Image.new("RGBA", (40, 30), (0, 0, 0, 0))
    monkeypatch.setattr(renderer, "prepare_transformed_layer", lambda *_args, **_kwargs: None)
    renderer.composite_transformed(canvas, (layer, (4, 3)), object_data)
    assert canvas.getbbox() is None
    monkeypatch.setattr(renderer, "prepare_transformed_layer", lambda *_args, **_kwargs: PreparedLayer(layer, (2, 3)))
    renderer.composite_transformed(canvas, (layer, (4, 3)), object_data)
    assert canvas.getbbox() is not None


def test_canvas_clipped_transform_covers_inside_crop_outside_and_rotation(tmp_path: Path):
    renderer = _renderer(tmp_path)
    renderer.canvas_w = 20
    renderer.canvas_h = 16
    layer = Image.new("RGBA", (10, 8), (0, 255, 0, 255))

    inside = renderer.prepare_canvas_clipped_transformed_layer(layer, (5, 4), 0, 10, 8, False)
    assert inside == PreparedLayer(layer, (5, 4))
    cropped = renderer.prepare_canvas_clipped_transformed_layer(layer, (5, 4), 0, 1, 1, False)
    assert cropped is not None
    assert cropped.image.size < layer.size
    assert renderer.prepare_canvas_clipped_transformed_layer(layer, (5, 4), 0, -20, -20, False) is None

    rotated = renderer.prepare_canvas_clipped_transformed_layer(layer, (5, 4), 30, 10, 8, False)
    assert rotated is not None
    supersampled = renderer.prepare_canvas_clipped_transformed_layer(layer, (5, 4), 30, 10, 8, True)
    assert supersampled is not None
    assert renderer.prepare_canvas_clipped_transformed_layer(layer, (5, 4), 30, -20, -20, False) is None


def test_full_transform_rotation_supersample_and_unity_point(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    renderer.origin_x = 10
    renderer.origin_y = 20
    renderer.position_scale_x = 2
    renderer.position_scale_y = 3
    assert renderer.unity_point({"x": 4, "y": 5}) == (18, 5)

    layer = Image.new("RGBA", (10, 8), (0, 0, 255, 255))
    monkeypatch.setattr(renderer_mod, "LAYER_ROTATION_SUPERSAMPLE", 2.0)
    ordinary = renderer.prepare_full_transformed_layer(layer, (5, 4), 0, 12, 9, False)
    supersampled = renderer.prepare_full_transformed_layer(layer, (5, 4), 25, 12, 9, True)
    assert ordinary.xy == (7, 5)
    assert supersampled.image.getbbox() is not None


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["--full-canvas"], (renderer_mod.CANVAS_W, renderer_mod.CANVAS_H, None, None)),
        (["--viewer-viewport"], (renderer_mod.PROFILE_RENDER_VIEW_W, renderer_mod.PROFILE_RENDER_VIEW_H, 0.0, 0.0)),
        ([], (renderer_mod.PROFILE_RENDER_VIEW_W, renderer_mod.PROFILE_RENDER_VIEW_H, None, None)),
    ],
)
def test_resolve_render_target_modes(arguments, expected):
    args = renderer_mod.build_arg_parser().parse_args(arguments)
    target = renderer_mod.resolve_render_target(args)
    assert (target.canvas_w, target.canvas_h) == expected[:2]
    if arguments == ["--full-canvas"]:
        assert (target.origin_x, target.origin_y) == expected[2:]
    elif arguments == ["--viewer-viewport"]:
        assert target.position_scale_x == renderer_mod.PROFILE_POSITION_SCALE_X
        assert target.position_scale_y == renderer_mod.PROFILE_POSITION_SCALE_Y
    else:
        assert target.position_scale_x is None
        assert target.position_scale_y is None
