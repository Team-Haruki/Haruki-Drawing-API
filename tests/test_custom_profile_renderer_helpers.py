import builtins
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from src.sekai.profile.custom_profile import renderer as renderer_mod
from src.sekai.profile.custom_profile.renderer import (
    PNGRenderer,
    StyledLine,
    _append_general_token,
    _choice_or_default,
    _coerced_resource_entry,
    _dedupe_paths,
    _default_if_none,
    _first_truthy,
    _float_first,
    _game_assets_root,
    _general_text_tokens,
    _int_first,
    _mapping_or_empty,
    _nonempty_strings,
    _optional_dict,
    _png_resource_filename,
    _positive_float,
    _positive_int,
    _record_or_noop,
    _resource_entries,
    alpha_mask_to_sdf_field,
    bool_from_profile,
    content_data_id,
    content_type_for_kind,
    edt_1d_squared,
    edt_to_features,
    harden_rgba_alpha,
    hex_to_rgba,
    int_or_none,
    is_large_background_block,
    is_tmp_block_char,
    is_tmp_em_block,
    largest_component_mask,
    premultiply_rgba_image,
    resize_rgba_premul,
    rotate_layer_about_pivot,
    scale_tmp_spacing,
    sdf_threshold_alpha,
    select_cards,
    sharp_triangle_alpha,
    sharp_triangle_distance,
    smoothstep,
    split_runs_by_line_with_style,
    summarize_card_master,
    summarize_honor_group,
    summarize_honor_master,
    summarize_resource,
    tmp_content_offset_y,
    tmp_horizontal_alignment,
    tmp_line_height,
    tmp_line_offset_x,
    tmp_native_anchor_y,
    tmp_vertical_alignment,
    transform_rgba_premul,
    trim_layer_to_content,
    unity_draw_order,
    unity_tint_rgba,
    unpremultiply_rgba_image,
)
from src.sekai.profile.custom_profile.svg import TextBreak, TextRun, TextStyle, TextStyleMarker


def _style(**changes: object) -> TextStyle:
    style = TextStyle(
        color="#112233",
        alpha=0.75,
        size=20.0,
        scale_x=1.0,
        cspace=1.0,
        mspace=2.0,
        indent=3.0,
        line_indent=4.0,
        line_height=5.0,
        rotate=0.0,
        voffset=6.0,
        mark_color=None,
        bold=False,
        italic=False,
        underline=False,
        strike=False,
        indent_percent=None,
        line_indent_percent=None,
    )
    return replace(style, **changes)


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


def test_renderer_scalar_and_collection_helpers_cover_fallbacks(tmp_path: Path) -> None:
    marker: list[Path] = []
    recorder = _record_or_noop(marker.append)
    path = tmp_path / "asset"
    recorder(path)

    assert _mapping_or_empty({"a": 1}) == {"a": 1}
    assert _mapping_or_empty([]) == {}
    assert _optional_dict(None) == {}
    assert _default_if_none(None, 3) == 3
    assert _default_if_none(0, 3) == 0
    assert _choice_or_default("a", {"a"}, "b") == "a"
    assert _choice_or_default("x", {"a"}, "b") == "b"
    assert _positive_int(0, 4) == 4
    assert _positive_int(-2) == 1
    assert _positive_float(0, 4.5) == 4.5
    assert _positive_float(-2) == 1.0
    assert _game_assets_root(tmp_path / "custom_profile") == tmp_path
    assert _game_assets_root(tmp_path / "assets") == tmp_path / "assets"
    assert marker == [path]
    assert _record_or_noop(None)(path) is None
    assert _first_truthy(0, "", "kept", default="fallback") == "kept"
    assert _first_truthy(0, "", default="fallback") == "fallback"
    assert _float_first(None, "2.5") == 2.5
    assert _int_first(None, "3") == 3
    assert _nonempty_strings([0, "", "x", None]) == ["0", "x", "None"]


def test_renderer_resource_and_summary_helpers_normalize_inputs(tmp_path: Path) -> None:
    item = {"id": "2", "name": "kept", "ignored": True}

    assert _resource_entries({"items": [item]}) == [(None, item)]
    assert _resource_entries({"2": item}) == [("2", item)]
    assert _resource_entries([item]) == [(None, item)]
    assert _resource_entries("bad") == []
    assert _coerced_resource_entry(None, item) == (2, item)
    assert _coerced_resource_entry("3", {"name": "fallback id"}) == (3, {"name": "fallback id"})
    assert _coerced_resource_entry("bad", {}) is None
    assert _coerced_resource_entry(1, "bad") is None
    assert int_or_none("2") == 2
    assert int_or_none(None) is None
    assert _dedupe_paths([tmp_path / "a", tmp_path / "a", tmp_path / "b"]) == [tmp_path / "a", tmp_path / "b"]
    assert _png_resource_filename({}) is None
    assert _png_resource_filename({"fileName": "/asset"}) == "asset.png"
    assert _png_resource_filename({"fileName": "asset.PNG"}) == "asset.PNG"

    resource = {
        "id": 2,
        "name": "resource",
        "fileName": "asset",
        "ignored": True,
    }
    assert summarize_resource(None) is None
    assert summarize_resource(resource) == {"id": 2, "name": "resource", "fileName": "asset"}
    assert summarize_card_master(None) is None
    assert summarize_card_master({"id": 1, "prefix": "x", "ignored": True}) == {"id": 1, "prefix": "x"}
    assert summarize_honor_master(None) is None
    assert summarize_honor_master({"id": 1, "name": "h", "ignored": True}) == {"id": 1, "name": "h"}
    assert summarize_honor_group(None) is None
    assert summarize_honor_group({"id": 1, "honorType": "x", "ignored": True}) == {
        "id": 1,
        "honorType": "x",
    }


def test_renderer_text_wrapping_and_profile_value_helpers() -> None:
    assert _general_text_tokens("abc.def/1 中文") == ["abc.def/1", " ", "中", "文"]
    lines: list[str] = []
    width = len
    line = _append_general_token("ab", "", 3, width, lines)
    line = _append_general_token("cd", line, 3, width, lines)
    line = _append_general_token("wxyz", line, 3, width, lines)
    assert lines == ["ab", "cd", "wxy"]
    assert line == "z"

    assert bool_from_profile(True)
    assert bool_from_profile("YES")
    assert not bool_from_profile("no")
    assert bool_from_profile(1)
    assert content_type_for_kind("missing") == (0, "Invalid")
    assert content_data_id("general", {"type": 2, "id": 3}) == 2
    assert content_data_id("stamp", {"stampId": 4}) == 4
    assert content_data_id("shape", {"id": 5}) == 5
    assert hex_to_rgba("#123456", 2.0) == (18, 52, 86, 255)
    assert hex_to_rgba("invalid", -1.0) == (255, 255, 255, 0)
    assert unity_tint_rgba((0.5, 1.0, -1.0, 2.0)) == (128, 255, 0, 255)
    assert unity_tint_rgba((300, 20, -1, 128)) == (255, 20, 0, 128)


def test_distance_field_and_alpha_math_cover_numpy_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    np = pytest.importorskip("numpy")
    assert edt_1d_squared(np.array([], dtype=np.float32)).size == 0
    assert edt_1d_squared(np.array([0.0, 10.0, 0.0], dtype=np.float32)).tolist() == pytest.approx([0, 1, 0])
    assert np.isinf(edt_to_features(np.zeros((2, 3), dtype=bool))).all()
    features = edt_to_features(np.array([[False, True], [False, False]]))
    np.testing.assert_allclose(features, [[1.0, 0.0], [2**0.5, 1.0]])

    original_import = builtins.__import__

    def import_without_cv2(name: str, *args: object, **kwargs: object):
        if name == "cv2":
            raise ImportError("cv2 disabled for fallback coverage")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_cv2)
    mask = Image.new("L", (3, 3), 0)
    mask.putpixel((1, 1), 255)
    field = alpha_mask_to_sdf_field(mask, 2.0)
    assert field.shape == (3, 3)
    assert 0.0 <= float(field.min()) <= float(field.max()) <= 1.0
    assert sharp_triangle_distance((8, 6)).size == (8, 6)
    assert largest_component_mask(mask).getextrema() == (255, 255)


def test_rgba_math_preserves_alpha_and_exercises_fast_and_premultiplied_paths() -> None:
    source = Image.new("RGBA", (2, 1), (100, 50, 25, 128))
    source.putpixel((1, 0), (0, 0, 0, 0))
    premultiplied = premultiply_rgba_image(source)
    restored = unpremultiply_rgba_image(premultiplied)

    assert premultiplied.getpixel((0, 0)) == (50, 25, 13, 128)
    assert restored.getpixel((0, 0))[:2] == pytest.approx((100, 50), abs=1)
    assert restored.getpixel((1, 0)) == (0, 0, 0, 0)
    assert harden_rgba_alpha(source, 1.0) is source
    assert harden_rgba_alpha(Image.new("RGB", (1, 1)), 2.0).mode == "RGB"
    transparent = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    assert harden_rgba_alpha(transparent, 2.0) is transparent
    assert harden_rgba_alpha(source, 2.0).getpixel((0, 0))[3] == 128

    opaque = Image.new("RGBA", (2, 2), (1, 2, 3, 255))
    assert resize_rgba_premul(opaque, (3, 3), Image.Resampling.BILINEAR).size == (3, 3)
    assert resize_rgba_premul(source, (3, 2), Image.Resampling.BILINEAR).size == (3, 2)
    identity = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    assert transform_rgba_premul(
        opaque,
        (2, 2),
        Image.Transform.AFFINE,
        identity,
        Image.Resampling.BILINEAR,
    ).size == (2, 2)
    assert transform_rgba_premul(
        source,
        (2, 1),
        Image.Transform.AFFINE,
        identity,
        Image.Resampling.BILINEAR,
        (20, 40, 60, 128),
    ).size == (2, 1)


def test_shape_and_alignment_helpers_cover_all_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    triangle = sharp_triangle_alpha((9, 7))
    assert triangle.size == (9, 7)
    assert triangle.getextrema()[1] == 255
    assert smoothstep(1.0, 1.0, 1.0) == 1.0
    assert smoothstep(1.0, 1.0, 0.0) == 0.0
    assert smoothstep(0.0, 1.0, 0.5) == 0.5
    assert sdf_threshold_alpha(Image.new("L", (1, 1), 128), 0.5, 0.1).getpixel((0, 0)) > 0

    assert [
        tmp_horizontal_alignment(value)
        for value in (
            renderer_mod.TMP_HORIZONTAL_CENTER,
            renderer_mod.TMP_HORIZONTAL_RIGHT,
            renderer_mod.TMP_HORIZONTAL_JUSTIFIED,
            renderer_mod.TMP_HORIZONTAL_FLUSH,
            renderer_mod.TMP_HORIZONTAL_GEOMETRY,
            0,
        )
    ] == ["center", "right", "justified", "flush", "geometry", "left"]
    assert [
        tmp_vertical_alignment(value)
        for value in (
            renderer_mod.TMP_VERTICAL_TOP,
            renderer_mod.TMP_VERTICAL_BOTTOM,
            renderer_mod.TMP_VERTICAL_BASELINE,
            renderer_mod.TMP_VERTICAL_GEOMETRY,
            renderer_mod.TMP_VERTICAL_CAPLINE,
            0,
        )
    ] == ["top", "bottom", "baseline", "geometry", "capline", "middle"]
    assert [tmp_line_offset_x(mode, 10, 4) for mode in ("center", "right", "left")] == [3, 6, 0]
    assert [tmp_content_offset_y(mode, 10, 4) for mode in ("top", "bottom", "middle")] == [0, 6, 3]
    assert [tmp_native_anchor_y(mode, 10, 4, -2) for mode in ("top", "bottom", "baseline", "middle")] == [
        1,
        -3,
        0,
        -1,
    ]


def test_shape_renderer_covers_sdf_dilate_ring_scale_and_plain_modes(tmp_path: Path, monkeypatch) -> None:
    renderer = _renderer(tmp_path)
    path = tmp_path / "shape.png"
    Image.new("L", (8, 6), 255).save(path)
    renderer.shapes = {1: {"fileName": "shape"}}
    renderer.colors = {1: "#112233", 2: "#445566"}
    monkeypatch.setattr(renderer, "shape_resource_path", lambda resource: path if resource else None)
    monkeypatch.setattr(renderer, "shape_alpha_mask", lambda *_: Image.new("L", (8, 6), 255))
    item = {
        "id": 1,
        "colorId": 1,
        "outlineColorId": 2,
        "alpha": 0.75,
        "outlineAlpha": 0.5,
        "outlineSize": 0.2,
        "objectData": {"scale": {"x": 2, "y": 0.5}},
    }

    assert renderer.render_shape({"id": 99}) is None
    renderer.shape_outline_mode = "sdf"
    renderer.shape_sdf_screen_fwidth = False
    monkeypatch.setattr(renderer, "render_distance_field_shape", lambda *_args: Image.new("RGBA", (9, 7)))
    sdf = renderer.render_shape(item)
    assert sdf is not None
    assert sdf[0].size == (9, 7)
    assert not sdf[2]

    renderer.shape_sdf_screen_fwidth = True
    calls: list[tuple[int, int] | None] = []
    monkeypatch.setattr(
        renderer,
        "render_distance_field_shape",
        lambda *_args: calls.append(_args[-1]) or Image.new("RGBA", _args[-1]),
    )
    scaled_sdf = renderer.render_shape(item)
    assert scaled_sdf is not None
    assert scaled_sdf[0].size == (16, 3)
    assert scaled_sdf[2]
    assert calls == [(16, 3)]

    for mode in ("dilate", "ring", "scale"):
        renderer.shape_outline_mode = mode
        rendered = renderer.render_shape(item)
        assert rendered is not None
        assert rendered[0].mode == "RGBA"
        assert not rendered[2]

    plain_item = dict(item, outlineAlpha=0.0, outlineSize=0.0)
    plain = renderer.render_shape(plain_item)
    assert plain is not None
    assert plain[0].size == (8, 6)


def test_distance_field_shape_renders_face_and_outline(tmp_path: Path, monkeypatch) -> None:
    np = pytest.importorskip("numpy")
    renderer = _renderer(tmp_path)
    renderer.shape_sdf_ratio_scale = 1.0
    renderer.shape_sdf_outer_factor = 1.0
    renderer.shape_sdf_face_factor = 1.0
    renderer.shape_sdf_softness = 0.1
    field = np.array([[0.1, 0.5], [0.9, 1.0]], dtype=np.float32)
    alpha = np.ones((2, 2), dtype=np.float32)
    fwidth = np.full((2, 2), 0.1, dtype=np.float32)
    monkeypatch.setattr(renderer, "shape_shader_arrays", lambda *_: (field, alpha, fwidth))

    image = renderer.render_distance_field_shape(
        tmp_path / "shape.png",
        "shape",
        "#112233",
        0.75,
        "#445566",
        0.5,
        0.3,
        (2, 2),
    )

    assert image.mode == "RGBA"
    assert image.size == (2, 2)
    assert image.getchannel("A").getextrema()[1] > 0


def test_text_line_split_order_scaling_selection_and_layer_geometry() -> None:
    base = _style()
    marked = replace(base, color="#ffffff")
    lines = split_runs_by_line_with_style(
        [TextRun("A\n", base), TextStyleMarker(marked), TextBreak(), TextRun("B", marked)],
        base,
    )
    assert [[run.text for run in line.runs] for line in lines] == [["A"], [], ["B"]]
    assert lines[0].trailing_newline_count == 1

    block = TextRun("■", replace(base, size=300))
    assert is_tmp_block_char(block)
    assert is_tmp_em_block(block)
    assert is_large_background_block([StyledLine([block], base)])
    assert not is_large_background_block([StyledLine([block, block], base)])
    assert tmp_line_height(20, 10, 2) >= 1
    assert scale_tmp_spacing(block, 1.0) is block
    scaled = scale_tmp_spacing(TextRun("A", base), 2.0)
    assert (scaled.style.cspace, scaled.style.mspace, scaled.style.voffset) == (2.0, 4.0, 12.0)

    elements = [(2, "text", {}), (1, "shape", {}), (0, "image", {})]
    assert [item[0] for item in unity_draw_order(elements, "global")] == [0, 1, 2]
    assert [item[1] for item in unity_draw_order(elements, "shapes-first")] == ["image", "shape", "text"]
    assert [item[1] for item in unity_draw_order(elements, "white-text-last")] == ["image", "shape", "text"]

    cards = [{"seq": 2, "customProfileCardId": 3}, {"seq": 1, "customProfileCardId": 2}]
    profile = {"userCustomProfileCards": cards}
    assert select_cards(profile, None, None, True) == [cards[1], cards[0]]
    assert select_cards(profile, None, 3, False) == [cards[0]]
    assert select_cards(profile, 2, None, False) == [cards[0]]
    assert select_cards(profile, None, None, False) == [cards[1]]

    layer = Image.new("RGBA", (10, 8), (0, 0, 0, 0))
    assert rotate_layer_about_pivot(layer, (5, 4), 0) == (layer, (5, 4))
    rotated, pivot = rotate_layer_about_pivot(layer, (5, 4), 90, premultiply_alpha=True)
    assert rotated.size == (8, 10)
    assert pivot == (4, 5)
    assert trim_layer_to_content(layer, (5, 4)) == (layer, (5, 4))
    layer.putpixel((5, 4), (255, 255, 255, 255))
    trimmed, trimmed_pivot = trim_layer_to_content(layer, (5, 4), pad=1)
    assert trimmed.size == (3, 3)
    assert trimmed_pivot == (1, 1)


def test_canvas_clipped_transform_handles_inside_crop_outside_and_rotation(tmp_path: Path) -> None:
    renderer = _renderer(tmp_path)
    renderer.canvas_w = 20
    renderer.canvas_h = 20
    layer = Image.new("RGBA", (8, 6), (255, 0, 0, 128))

    inside = renderer.prepare_canvas_clipped_transformed_layer(layer, (4, 3), 0, 10, 10, False)
    assert inside is not None
    assert inside.image is layer
    assert inside.xy == (6, 7)

    cropped = renderer.prepare_canvas_clipped_transformed_layer(layer, (4, 3), 0, 1, 1, False)
    assert cropped is not None
    assert cropped.image.size == (5, 4)
    assert cropped.xy == (0, 0)
    assert renderer.prepare_canvas_clipped_transformed_layer(layer, (4, 3), 0, -20, -20, False) is None
    assert renderer.prepare_canvas_clipped_transformed_layer(layer, (4, 3), 45, -20, -20, False) is None

    rotated = renderer.prepare_canvas_clipped_transformed_layer(layer, (4, 3), 45, 10, 10, False)
    assert rotated is not None
    assert rotated.image.size[0] > 0
    supersampled = renderer.prepare_canvas_clipped_transformed_layer(layer, (4, 3), 45, 10, 10, True)
    assert supersampled is not None
    assert supersampled.image.size == rotated.image.size


def test_tmp_native_layout_character_preserves_mesh_metrics_and_whitespace_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = _renderer(tmp_path)
    style = _style(mspace=10.0)
    metrics = renderer_mod.TMPGlyphMetrics(4.0, 6.0, -1.0, 5.0, 5.0, 0, 0, 4, 6, 1.0, 0)
    monkeypatch.setattr(renderer, "tmp_native_glyph_metrics", lambda *_args, **_kwargs: metrics)
    monkeypatch.setattr(renderer, "tmp_native_baseline_offset", lambda *_: 2.0)
    monkeypatch.setattr(renderer, "tmp_native_style_extents", lambda *_: (10.0, -3.0))
    monkeypatch.setattr(renderer, "tmp_native_visible_character", lambda char: not char.isspace())
    monkeypatch.setattr(renderer, "tmp_native_layout_advance_scale_x", lambda *_: 1.2)
    monkeypatch.setattr(renderer, "tmp_native_vertex_scale_x", lambda *_: 2.0)
    monkeypatch.setattr(renderer, "tmp_native_character_sdf_scale", lambda *_: 0.5)
    monkeypatch.setattr(renderer, "tmp_native_vertex_padding", lambda *_: 1.0)
    monkeypatch.setattr(renderer, "tmp_mspace_advance", lambda *_: 10.0)
    monkeypatch.setattr(
        renderer,
        "tmp_native_fx_quad",
        lambda left, right, top, bottom, *_: (left, bottom, left, top, right, top, right, bottom),
    )
    monkeypatch.setattr(renderer, "tmp_native_next_x_advance", lambda *_: 30.0)
    monkeypatch.setattr(renderer, "tmp_native_character_info_x_advance", lambda *_: 31.0)

    info, advance, ascender, descender, visible = renderer.tmp_native_layout_character(
        "A",
        style,
        "font",
        tmp_path / "font.ttf",
        0,
        0,
        10.0,
        4.0,
        0,
        -100.0,
        100.0,
        0,
        "tmp",
        1.0,
        0.2,
    )
    assert advance == 30.0
    assert (ascender, descender, visible) == (12.0, -3.0, 1)
    assert info.x_advance == 31.0
    assert info.glyph_origin_x == pytest.approx(12.5)
    assert info.baseline == -2.0
    assert info.visible
    assert info.sdf_scale == 0.5

    whitespace, _, ascender, descender, visible = renderer.tmp_native_layout_character(
        " ",
        replace(style, mspace=None),
        "font",
        tmp_path / "font.ttf",
        0,
        1,
        10.0,
        4.0,
        0,
        12.0,
        -4.0,
        1,
        "tmp",
        1.0,
        0.2,
        source_metrics_only=True,
    )
    assert (ascender, descender, visible) == (12.0, -4.0, 1)
    assert whitespace.ascender == 8.0
    assert whitespace.descender == -8.0
    assert not whitespace.visible


def test_prepare_direct_sdf_quads_runs_layout_audit_and_skips_clipped_glyphs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = _renderer(tmp_path)
    renderer.text_fonts = {1: "FixtureFont"}
    renderer.text_vertical_mode = "tmp-native"
    renderer.include_empty_lines = True
    style = _style(line_height=None)
    text_data = SimpleNamespace(text="A", font_id=1)
    mesh_state = SimpleNamespace(
        font_size=20.0,
        tmp_line_spacing=0.0,
        underlay_color="#000000",
        underlay_dilate=0.0,
        align=renderer_mod.TMP_HORIZONTAL_CENTER | renderer_mod.TMP_VERTICAL_TOP,
    )
    native_line_layout = SimpleNamespace(content_height=10.0)
    native_text_layout = SimpleNamespace(
        preferred_width=30.0,
        preferred_height=20.0,
        content_height=10.0,
        dominant_size=20.0,
    )
    mesh_text_layout = SimpleNamespace(
        line_layout=native_line_layout,
        accumulated_line_height=12.0,
        lines=[],
    )
    audited: list[tuple[object, ...]] = []
    monkeypatch.setattr(renderer, "generate_text_data", lambda _item: text_data)
    monkeypatch.setattr(renderer, "font_path_for", lambda *_: tmp_path / "font.ttf")
    monkeypatch.setattr(renderer, "update_text_mesh_state", lambda *_: mesh_state)
    monkeypatch.setattr(renderer, "base_text_style", lambda *_: style)
    monkeypatch.setattr(renderer, "decorative_outline_dilate", lambda *_: 0.0)
    monkeypatch.setattr(renderer, "resolve_tmp_text_box_layouts", lambda *_args, **_kwargs: None)
    assert renderer.prepare_direct_sdf_quads({}, {}) is None

    monkeypatch.setattr(
        renderer,
        "resolve_tmp_text_box_layouts",
        lambda *_args, **_kwargs: (native_text_layout, mesh_text_layout),
    )
    monkeypatch.setattr(renderer, "tmp_text_box_size", lambda *_: (40.0, 20.0))
    monkeypatch.setattr(renderer, "tmp_native_baseline_downs", lambda *_: [5.0])
    monkeypatch.setattr(renderer, "tmp_native_mesh_pixel_bounds", lambda *_: (-2.0, -3.0, 18.0, 17.0))
    monkeypatch.setattr(renderer, "record_tmp_layout_audit", lambda *args: audited.append(args))
    glyphs = [
        (Image.new("L", (2, 3), 255), None, style, 0.0, 0.0, (2, 3), None),
        (Image.new("L", (4, 5), 255), None, style, 2.0, 1.0, (4, 5), None),
    ]
    monkeypatch.setattr(renderer, "prepare_tmp_direct_sdf_glyphs", lambda *_args, **_kwargs: glyphs)
    prepared_calls: list[int] = []

    def prepare_quad(_glyph: object, _pivot: object, _object_data: object, *_args: object):
        prepared_calls.append(_args[-1])
        return (None if len(prepared_calls) == 1 else "quad", _args[-1] - 1)

    monkeypatch.setattr(renderer, "prepare_direct_sdf_quad", prepare_quad)

    assert renderer.prepare_direct_sdf_quads({}, {}) == ["quad"]
    assert audited
    assert prepared_calls == [26, 25]

    monkeypatch.setattr(renderer, "prepare_tmp_direct_sdf_glyphs", lambda *_args, **_kwargs: None)
    assert renderer.prepare_direct_sdf_quads({}, {}) is None
    monkeypatch.setattr(renderer, "generate_text_data", lambda _item: SimpleNamespace(text="   ", font_id=1))
    assert renderer.prepare_direct_sdf_quads({}, {}) is None
