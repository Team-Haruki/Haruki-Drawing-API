from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytest

from src.sekai.profile.custom_profile import renderer as renderer_mod
from src.sekai.profile.custom_profile.renderer import (
    PNGRenderer,
    StyledLine,
    TMPDynamicRunGlyph,
    TMPGlyphMetrics,
    TMPNativeCharacterInfo,
)
from src.sekai.profile.custom_profile.svg import TextRun, TextStyle


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


def _style(**changes: object) -> TextStyle:
    base = TextStyle(
        color="#336699",
        alpha=0.8,
        size=18.0,
        scale_x=1.0,
        cspace=0.0,
        mspace=None,
        indent=0.0,
        line_indent=0.0,
        line_height=None,
        rotate=0.0,
        voffset=0.0,
        mark_color=None,
        bold=False,
        italic=False,
        underline=False,
        strike=False,
    )
    return replace(base, **changes)


def _metrics(**changes: object) -> TMPGlyphMetrics:
    values = {
        "width": 5.0,
        "height": 7.0,
        "bearing_x": 1.0,
        "bearing_y": 6.0,
        "advance": 6.0,
        "rect_x": 1,
        "rect_y": 1,
        "rect_w": 4,
        "rect_h": 5,
        "glyph_scale": 1.0,
        "atlas_index": 0,
    }
    values.update(changes)
    return TMPGlyphMetrics(**values)


def _asset(tmp_path: Path, **changes: object) -> SimpleNamespace:
    values = {
        "name": "font",
        "bundle": "font",
        "source_font_path": tmp_path / "font.ttf",
        "atlas_paths": [tmp_path / "atlas.png"],
        "atlas_population_mode": 1,
        "atlas_width": 16.0,
        "atlas_height": 16.0,
        "atlas_padding": 2.0,
        "point_size": 16.0,
        "face_scale": 1.0,
        "line_height": 18.0,
        "ascent_line": 12.0,
        "descent_line": -4.0,
        "tab_width": 4.0,
        "gradient_scale": 6.0,
        "weight_normal": 0.0,
        "weight_bold": 0.75,
        "face_dilate": 0.0,
        "outline_width": 0.0,
        "outline_softness": 0.0,
        "sharpness": 0.0,
        "normal_spacing_offset": 0.0,
        "bold_spacing": 0.0,
        "scale_ratio_a": 1.0,
        "scale_ratio_b": 1.0,
        "scale_ratio_c": 1.0,
        "glow_offset": 0.0,
        "glow_outer": 0.0,
        "underlay_softness": 0.0,
        "underlay_offset_x": 0.0,
        "underlay_offset_y": 0.0,
        "fallback_names": [],
        "glyphs": {ord("A"): _metrics(), ord("B"): _metrics(rect_x=6)},
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _character(style: TextStyle, *, visible: bool = True, char: str = "A") -> TMPNativeCharacterInfo:
    return TMPNativeCharacterInfo(
        index=0,
        char=char,
        line_index=0,
        x_origin=2.0,
        x_advance=8.0,
        glyph_origin_x=2.0,
        bottom_left_x=1.0,
        bottom_left_y=-2.0,
        top_left_x=1.0,
        top_left_y=7.0,
        top_right_x=7.0,
        top_right_y=7.0,
        bottom_right_x=7.0,
        bottom_right_y=-2.0,
        vertex_padding=1.0,
        raw_left_x=1.0,
        raw_right_x=7.0,
        raw_top_y=7.0,
        raw_bottom_y=-2.0,
        baseline=0.0,
        ascender=7.0,
        descender=-2.0,
        adjusted_ascender=7.0,
        adjusted_descender=-2.0,
        visible=visible,
        style=style,
        metrics=_metrics(),
        sdf_scale=1.0,
    )


def test_tmp_run_audit_and_mask_drawers_cover_spacing_scaling_and_visibility(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    style = _style(mspace=9.0, cspace=2.0, scale_x=1.5)
    run = TextRun("A B", style)
    font = ImageFont.load_default(size=14)
    renderer.tmp_scale_mode = "fx-center"
    monkeypatch.setattr(renderer, "glyph_layout_metrics_with_source", lambda *_args: (_metrics(), "fixture"))
    monkeypatch.setattr(renderer, "tmp_native_visible_character", lambda ch: not ch.isspace())
    monkeypatch.setattr(renderer, "tmp_character_spacing_advance", lambda *_args: 2.0)
    monkeypatch.setattr(renderer, "tmp_render_glyph_char", lambda _name, ch, _size: ch)
    monkeypatch.setattr(renderer, "glyph_advance", lambda *_args: 6.0)

    audit = renderer.tmp_run_glyph_audit(font, run, "font", 14)
    assert [entry["metricSource"] for entry in audit] == ["fixture"] * 3
    assert audit[0]["advance"] == 9.0
    assert audit[-1]["postCharacterSpacing"] == 0.0
    assert audit[0]["advanceScaleX"] == 1.5

    rgba = Image.new("RGBA", (80, 30), (0, 0, 0, 0))
    renderer.draw_text_run(
        ImageDraw.Draw(rgba),
        (2, 2),
        run,
        font,
        (255, 0, 0, 255),
        1,
        (0, 0, 0, 255),
        "font",
        14,
    )
    assert rgba.getbbox() is not None

    mask = Image.new("L", (80, 30), 0)
    renderer.draw_text_mask_run(ImageDraw.Draw(mask), (2, 2), run, font, "font", 14)
    assert mask.getbbox() is not None

    renderer.tmp_scale_mode = "x"
    scaled_mask = Image.new("L", (120, 40), 0)
    renderer.draw_text_mask_run_fx(scaled_mask, (5, 4), run, font, "font", 14)
    assert scaled_mask.getbbox() is not None

    renderer.tmp_scale_mode = "uniform"
    plain_mask = Image.new("L", (80, 30), 0)
    renderer.draw_text_mask_run_fx(plain_mask, (2, 2), run, font, "font", 14)
    assert plain_mask.getbbox() is not None


def test_static_atlas_placement_field_and_render_paths(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    asset = _asset(tmp_path)
    run = TextRun("AB", _style(cspace=1.0))
    monkeypatch.setattr(renderer, "tmp_render_glyph_char", lambda _name, ch, _size: ch)
    monkeypatch.setattr(renderer, "tmp_native_visible_character", lambda ch: not ch.isspace())
    monkeypatch.setattr(renderer, "tmp_character_spacing_advance", lambda *_args: 1.0)
    monkeypatch.setattr(renderer, "tmp_atlas_alpha", lambda _path: Image.new("L", (16, 16), 220))
    monkeypatch.setattr(renderer, "tmp_static_sdf_asset", lambda *_args: asset)
    monkeypatch.setattr(renderer, "tmp_display_padding", lambda *_args: 2)
    monkeypatch.setattr(
        renderer,
        "shade_tmp_sdf_field",
        lambda field, *_args: Image.new("RGBA", (field.shape[1], field.shape[0]), (10, 20, 30, 255)),
    )

    prepared = renderer.tmp_static_atlas_placements("font", run, 16, asset)
    assert prepared is not None
    placements, bbox = prepared
    assert len(placements) == 2
    field = renderer.tmp_static_atlas_field(asset, placements, bbox, 2)
    assert field.getbbox() is not None
    rendered = renderer.render_tmp_static_atlas_run("font", run, 16, "#000000", 0.0)
    assert rendered is not None
    assert rendered[0].mode == "RGBA"

    renderer.tmp_scale_mode = "x"
    scaled = renderer.render_tmp_static_atlas_run(
        "font", TextRun("A", replace(run.style, scale_x=2.0)), 16, "#000000", 0.0
    )
    assert scaled is not None
    assert scaled[0].width > rendered[0].width / 2

    assert renderer.tmp_static_atlas_placements("font", TextRun("Z", run.style), 16, asset) is None
    assert renderer.tmp_static_atlas_placements("font", TextRun(" ", run.style), 16, asset) is None
    monkeypatch.setattr(renderer, "tmp_static_sdf_asset", lambda *_args: None)
    assert renderer.render_tmp_static_atlas_run("font", run, 16, "#000000", 0.0) is None


def test_dynamic_sdf_run_rasterizes_scales_and_fails_open(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    asset = _asset(tmp_path)
    run = TextRun("AB", _style(scale_x=1.5))
    source_path = tmp_path / "source.ttf"
    font = ImageFont.load_default(size=16)
    renderer.tmp_font_library = SimpleNamespace(runtime_source_font_path=lambda _asset: source_path)
    monkeypatch.setattr(renderer, "tmp_sdf_asset", lambda _name: asset)
    monkeypatch.setattr(renderer_mod, "load_font", lambda *_args: font)
    monkeypatch.setattr(renderer, "run_bbox", lambda *_args: (0, 0, 12, 8))
    monkeypatch.setattr(renderer, "run_fx_bbox", lambda *_args: (-1, 0, 13, 8))
    monkeypatch.setattr(renderer, "tmp_shader_padding", lambda *_args, **_kwargs: 2.0)
    monkeypatch.setattr(renderer, "draw_text_mask_run", lambda draw, *_args: draw.rectangle((1, 1, 8, 6), fill=255))
    monkeypatch.setattr(
        renderer,
        "draw_text_mask_run_fx",
        lambda target, *_args: ImageDraw.Draw(target).rectangle((1, 1, 8, 6), fill=255),
    )
    monkeypatch.setattr(renderer_mod, "alpha_mask_to_sdf_field", lambda mask, *_args: np.asarray(mask) / 255.0)
    monkeypatch.setattr(
        renderer,
        "shade_tmp_sdf_field",
        lambda field, *_args: Image.new("RGBA", (field.shape[1], field.shape[0]), (255, 0, 0, 255)),
    )

    normal = renderer.render_tmp_dynamic_sdf_run("font", source_path, run, 16, "#000000", 0.0)
    assert normal is not None
    assert normal[0].getbbox() is not None

    renderer.tmp_scale_mode = "fx-center"
    fx = renderer.render_tmp_dynamic_sdf_run("font", source_path, run, 16, "#000000", 0.0)
    assert fx is not None

    renderer.tmp_font_library.runtime_source_font_path = lambda _asset: None
    assert renderer.render_tmp_dynamic_sdf_run("font", source_path, run, 16, "#000000", 0.0) is None
    monkeypatch.setattr(renderer, "tmp_sdf_asset", lambda _name: None)
    assert renderer.render_tmp_dynamic_sdf_run("font", source_path, run, 16, "#000000", 0.0) is None

    monkeypatch.setattr(renderer, "tmp_sdf_asset", lambda _name: asset)
    renderer.tmp_font_library.runtime_source_font_path = lambda _asset: source_path
    monkeypatch.setattr(renderer_mod, "alpha_mask_to_sdf_field", lambda *_args: (_ for _ in ()).throw(ImportError()))
    assert renderer.render_tmp_dynamic_sdf_run("font", source_path, run, 16, "#000000", 0.0) is None


def test_vector_sdf_field_handles_contours_degenerate_edges_and_missing(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    asset = _asset(tmp_path, gradient_scale=5.0)
    contours = (
        np.asarray([(0, 0), (6, 0), (6, 6), (0, 6)], dtype=np.float32),
        np.asarray([(2, 2), (2, 2), (4, 4)], dtype=np.float32),
    )
    monkeypatch.setattr(renderer, "tmp_vector_glyph_contours", lambda *_args: (contours, np))
    field = renderer.tmp_vector_glyph_sdf_field(tmp_path / "font.ttf", "A", 16, (0, -6, 6, 0), 2, asset)
    assert field is not None
    assert field.mode == "L"
    assert field.getextrema()[1] > field.getextrema()[0]

    monkeypatch.setattr(renderer, "tmp_vector_glyph_contours", lambda *_args: None)
    assert renderer.tmp_vector_glyph_sdf_field(tmp_path / "font.ttf", "A", 16, (0, 0, 2, 2), 1, asset) is None


def test_dynamic_glyph_builder_prefers_vector_then_raster_and_import_fallback(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    asset = _asset(tmp_path)
    source_path = tmp_path / "font.ttf"
    bounds = ((0, -8, 6, 2), Image.new("L", (6, 10), 255), 0, 8)
    monkeypatch.setattr(renderer_mod, "freetype_metrics", lambda: object())
    monkeypatch.setattr(renderer, "tmp_dynamic_glyph_bounds", lambda *_args: bounds)
    monkeypatch.setattr(renderer, "tmp_vector_glyph_sdf_field", lambda *_args: Image.new("L", (12, 16), 128))
    vector = renderer.build_tmp_dynamic_glyph_sdf(source_path, "A", 16, asset)
    assert vector is not None
    assert vector.field.size == (12, 16)

    monkeypatch.setattr(renderer, "tmp_vector_glyph_sdf_field", lambda *_args: None)
    monkeypatch.setattr(renderer, "tmp_dynamic_glyph_mask", lambda *_args: Image.new("L", (12, 16), 255))
    monkeypatch.setattr(renderer_mod, "alpha_mask_to_sdf_field", lambda mask, *_args: np.asarray(mask) / 255.0)
    raster = renderer.build_tmp_dynamic_glyph_sdf(source_path, "A", 16, asset)
    assert raster is not None
    assert raster.field.mode == "L"

    monkeypatch.setattr(renderer, "tmp_dynamic_glyph_bounds", lambda *_args: None)
    assert renderer.build_tmp_dynamic_glyph_sdf(source_path, "A", 16, asset) is None

    calls = iter([bounds, None])
    monkeypatch.setattr(renderer, "tmp_dynamic_glyph_bounds", lambda *_args: next(calls))
    assert renderer.build_tmp_dynamic_glyph_sdf(source_path, "A", 16, asset) is None

    monkeypatch.setattr(renderer, "tmp_dynamic_glyph_bounds", lambda *_args: bounds)
    monkeypatch.setattr(renderer_mod, "alpha_mask_to_sdf_field", lambda *_args: (_ for _ in ()).throw(ImportError()))
    assert renderer.build_tmp_dynamic_glyph_sdf(source_path, "A", 16, asset) is None


def test_dynamic_run_glyph_pipeline_composes_and_scales(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    asset = _asset(tmp_path)
    renderer.tmp_dynamic_sdf = True
    renderer.tmp_scale_mode = "fx-center"
    run = TextRun("AB", _style(scale_x=1.5, cspace=1.0))
    font = ImageFont.load_default(size=16)
    monkeypatch.setattr(renderer, "tmp_sdf_asset", lambda _name: asset)
    monkeypatch.setattr(renderer_mod, "load_font", lambda *_args: font)
    monkeypatch.setattr(renderer, "tmp_render_glyph_char", lambda _name, ch, _size: ch)
    monkeypatch.setattr(renderer, "tmp_dynamic_run_glyph_advance", lambda _font, _ch, *_args: (_args[-1], 6.0))
    monkeypatch.setattr(renderer, "tmp_character_spacing_advance", lambda *_args: 1.0)

    def prepare(_font_name, _font_path, glyph_char, _style, _size, origin, *_args):
        if glyph_char == "B":
            return None
        return TMPDynamicRunGlyph(Image.new("RGBA", (8, 10), (255, 0, 0, 255)), (0, -8, 6, 2), 1, origin)

    monkeypatch.setattr(renderer, "prepare_tmp_dynamic_run_glyph", prepare)
    result = renderer.render_tmp_dynamic_sdf_run_from_glyphs("font", tmp_path / "font.ttf", run, 16, "#000000", 0.0)
    assert result is not None
    assert result[0].getbbox() is not None

    renderer.tmp_scale_mode = "x"
    scaled = renderer.compose_tmp_dynamic_run_glyphs(
        [TMPDynamicRunGlyph(Image.new("RGBA", (8, 10), "red"), (0, -8, 6, 2), 1, 0.0)],
        (0, -8, 6, 2),
        1,
        run.style,
    )
    assert scaled[0].width > 8
    assert scaled[1][2] == 9

    monkeypatch.setattr(renderer, "prepare_tmp_dynamic_run_glyph", lambda *_args: None)
    assert (
        renderer.render_tmp_dynamic_sdf_run_from_glyphs("font", tmp_path / "font.ttf", run, 16, "#000000", 0.0) is None
    )
    asset.atlas_population_mode = 0
    assert (
        renderer.render_tmp_dynamic_sdf_run_from_glyphs("font", tmp_path / "font.ttf", run, 16, "#000000", 0.0) is None
    )


def test_render_tmp_sdf_run_fallback_modes_and_import_error(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    asset = _asset(tmp_path)
    run = TextRun("A", _style(scale_x=1.5))
    font = ImageFont.load_default(size=16)
    shaded = Image.new("RGBA", (8, 8), "red")
    monkeypatch.setattr(renderer, "render_tmp_static_atlas_run", lambda *_args: None)
    monkeypatch.setattr(renderer, "render_tmp_dynamic_sdf_run_from_glyphs", lambda *_args: None)
    monkeypatch.setattr(renderer, "render_tmp_dynamic_sdf_run", lambda *_args: None)
    monkeypatch.setattr(renderer_mod, "load_font", lambda *_args: font)
    monkeypatch.setattr(renderer, "run_bbox", lambda *_args: (0, 0, 6, 8))
    monkeypatch.setattr(renderer, "run_fx_bbox", lambda *_args: (-1, 0, 7, 8))
    monkeypatch.setattr(renderer, "tmp_sdf_asset", lambda _name: asset)
    monkeypatch.setattr(renderer, "tmp_sdf_spread", lambda *_args: 4.0)
    monkeypatch.setattr(renderer, "tmp_display_padding", lambda *_args: 2)
    monkeypatch.setattr(renderer, "draw_text_mask_run", lambda draw, *_args: draw.rectangle((1, 1, 5, 6), fill=255))
    monkeypatch.setattr(
        renderer,
        "draw_text_mask_run_fx",
        lambda target, *_args: ImageDraw.Draw(target).rectangle((1, 1, 5, 6), fill=255),
    )
    monkeypatch.setattr(renderer_mod, "alpha_mask_to_sdf_field", lambda mask, *_args: np.asarray(mask) / 255.0)
    monkeypatch.setattr(renderer, "shade_tmp_sdf_field", lambda *_args: shaded)

    renderer.tmp_dynamic_sdf = True
    assert renderer.render_tmp_sdf_run("font", tmp_path / "font.ttf", run, 16, "#000000", 0.0)[0] is shaded
    renderer.tmp_scale_mode = "x"
    assert renderer.render_tmp_sdf_run("font", tmp_path / "font.ttf", run, 16, "#000000", 0.0) is not None
    renderer.tmp_scale_mode = "fx-center"
    assert renderer.render_tmp_sdf_run("font", tmp_path / "font.ttf", run, 16, "#000000", 0.0) is not None

    monkeypatch.setattr(renderer, "render_tmp_static_atlas_run", lambda *_args: (shaded, (0, 0, 1, 1), 1))
    assert renderer.render_tmp_sdf_run("font", tmp_path / "font.ttf", run, 16, "#000000", 0.0)[0] is shaded
    monkeypatch.setattr(renderer, "render_tmp_static_atlas_run", lambda *_args: None)
    monkeypatch.setattr(renderer_mod, "alpha_mask_to_sdf_field", lambda *_args: (_ for _ in ()).throw(ImportError()))
    assert renderer.render_tmp_sdf_run("font", tmp_path / "font.ttf", run, 16, "#000000", 0.0) is None


def test_native_character_and_run_drawers_cover_em_sdf_and_pillow_paths(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    target = Image.new("RGBA", (120, 80), (0, 0, 0, 0))
    base_style = _style()
    invisible = _character(base_style, visible=False)
    renderer.draw_tmp_native_character(target, "font", tmp_path / "font.ttf", invisible, 10, 40, "#000", 1, 0)
    assert target.getbbox() is None

    glyph = Image.new("RGBA", (8, 10), "red")
    monkeypatch.setattr(renderer, "use_em_block", lambda _run: True)
    monkeypatch.setattr(renderer, "render_em_block_glyph", lambda *_args: (glyph, 1.0, _metrics()))
    monkeypatch.setattr(renderer, "tmp_native_baseline_offset", lambda _style: 0.0)
    renderer.draw_tmp_native_character(
        target, "font", tmp_path / "font.ttf", _character(base_style), 10, 40, "#000", 1, 0
    )
    assert target.getbbox() is not None

    target = Image.new("RGBA", (120, 80), (0, 0, 0, 0))
    monkeypatch.setattr(renderer, "use_em_block", lambda _run: False)
    monkeypatch.setattr(renderer, "render_tmp_sdf_character_image", lambda *_args: None)
    fallback_calls: list[tuple[float, float]] = []
    monkeypatch.setattr(
        renderer,
        "draw_run_at_baseline",
        lambda _target, _name, _path, _run, x, y, *_args: fallback_calls.append((x, y)),
    )
    renderer.draw_tmp_native_character(
        target, "font", tmp_path / "font.ttf", _character(base_style), 10, 40, "#000", 1, 0
    )
    assert fallback_calls == [(12.0, 40)]

    monkeypatch.setattr(
        renderer,
        "render_tmp_sdf_character_image",
        lambda *_args: (Image.new("RGBA", (4, 5), "blue"), (0, 0, 4, 5), 1, 1),
    )
    plain_target = Image.new("RGBA", (120, 80), (0, 0, 0, 0))
    renderer.draw_tmp_native_character(
        plain_target, "font", tmp_path / "font.ttf", _character(base_style), 10, 40, "#000", 1, 0
    )
    assert plain_target.getbbox() is not None
    rotated_target = Image.new("RGBA", (120, 80), (0, 0, 0, 0))
    renderer.draw_tmp_native_character(
        rotated_target,
        "font",
        tmp_path / "font.ttf",
        _character(replace(base_style, rotate=20.0)),
        10,
        40,
        "#000",
        1,
        0,
    )
    assert rotated_target.getbbox() is not None

    monkeypatch.setattr(renderer_mod, "load_font", lambda *_args: ImageFont.load_default(size=14))
    monkeypatch.setattr(renderer, "run_bbox", lambda *_args: (0, 0, 8, 10))
    monkeypatch.setattr(renderer, "draw_text_run", lambda draw, *_args: draw.rectangle((1, 1, 6, 8), fill="white"))
    monkeypatch.setattr(renderer, "run_y", lambda *_args: 5.0)
    monkeypatch.setattr(renderer, "run_y_from_baseline", lambda *_args: 5.0)
    renderer.tmp_text_render_mode = "pillow"
    renderer.tmp_scale_mode = "x"
    painter_run = TextRun("A", replace(base_style, scale_x=1.5, rotate=10.0))
    renderer.draw_run(target, "font", tmp_path / "font.ttf", painter_run, 10, 5, 20, "#000", 1, 0)
    renderer_mod.PNGRenderer.draw_run_at_baseline(
        renderer, target, "font", tmp_path / "font.ttf", painter_run, 10, 30, "#000", 1, 0
    )

    renderer.tmp_text_render_mode = "sdf"
    monkeypatch.setattr(renderer, "render_tmp_sdf_run", lambda *_args: (glyph, (0, 0, 8, 10), 1))
    renderer.draw_run(target, "font", tmp_path / "font.ttf", painter_run, 20, 5, 20, "#000", 1, 0)
    renderer_mod.PNGRenderer.draw_run_at_baseline(
        renderer, target, "font", tmp_path / "font.ttf", painter_run, 20, 30, "#000", 1, 0
    )

    monkeypatch.setattr(renderer, "use_em_block", lambda _run: True)
    monkeypatch.setattr(renderer, "render_em_block_glyph", lambda *_args: (glyph, 1.0, _metrics()))
    monkeypatch.setattr(renderer, "tmp_face_baseline_offset", lambda *_args: 9.0)
    renderer_mod.PNGRenderer.draw_run(
        renderer, target, "font", tmp_path / "font.ttf", painter_run, 30, 5, 20, "#000", 1, 0
    )
    renderer_mod.PNGRenderer.draw_run_at_baseline(
        renderer, target, "font", tmp_path / "font.ttf", painter_run, 30, 30, "#000", 1, 0
    )

    monkeypatch.setattr(renderer, "render_em_block_glyph", lambda *_args: (glyph, 1.0, None))
    renderer_mod.PNGRenderer.draw_run(
        renderer, target, "font", tmp_path / "font.ttf", painter_run, 40, 5, 20, "#000", 1, 0
    )
    renderer_mod.PNGRenderer.draw_run_at_baseline(
        renderer, target, "font", tmp_path / "font.ttf", painter_run, 40, 30, "#000", 1, 0
    )
    assert target.getbbox() is not None


def test_native_line_measurement_and_visual_metrics_cover_em_source_and_pillow(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    first = TextRun("A", _style(cspace=-6.0))
    second = TextRun("B", _style(size=24.0))
    line = StyledLine([first, second], first.style)
    visual = renderer_mod.TMPRunVisualMetrics(6.0, -1.0, 5.0, -7.0, 2.0)
    monkeypatch.setattr(renderer, "tmp_native_vertex_padding", lambda *_args: 1.0)
    monkeypatch.setattr(renderer, "tmp_native_run_visual_metrics", lambda *_args, **_kwargs: visual)
    monkeypatch.setattr(renderer, "tmp_native_run_advance", lambda run, *_args, **_kwargs: float(len(run.text) * 6))
    monkeypatch.setattr(renderer, "tmp_inter_run_spacing_advance", lambda *_args: 2.0)

    run_metrics, width, left, right, dominant = renderer.tmp_native_measure_line_runs(
        line, "font", tmp_path / "font.ttf", 0, "mesh", 1.0, 0.2, 100
    )
    assert len(run_metrics) == 2
    assert width >= 14.0
    assert left < right
    assert dominant == 24.0

    empty = StyledLine([], _style(indent=-3.0))
    empty_metrics = renderer.tmp_native_measure_line_runs(
        empty, "font", tmp_path / "font.ttf", 0, "mesh", 1.0, 0.2, 100
    )
    assert empty_metrics[:2] == ([], 1.0)
    assert empty_metrics[2:4] == (-3.0, 0.0)

    em_run = TextRun("■", _style(size=20.0))
    monkeypatch.setattr(renderer, "use_em_block", lambda run: run is em_run)
    monkeypatch.setattr(renderer, "tmp_source_block_metrics", lambda *_args: _metrics(width=8, advance=9, bearing_x=-1))
    monkeypatch.setattr(renderer, "tmp_native_style_extents", lambda *_args: (12.0, -3.0))
    visual_metrics = renderer_mod.PNGRenderer.tmp_native_run_visual_metrics
    em_visual = visual_metrics(renderer, em_run, "font", tmp_path / "font.ttf", 20, 1.0)
    assert em_visual == renderer_mod.TMPRunVisualMetrics(9, -1, 7, -12.0, 3.0)

    monkeypatch.setattr(renderer, "tmp_source_block_metrics", lambda *_args: None)
    fallback_visual = visual_metrics(renderer, em_run, "font", tmp_path / "font.ttf", 20, 1.0)
    assert fallback_visual.advance == 20

    regular = TextRun("A", _style())
    measure = renderer_mod.TMPRunMeasure(6, -1, 5, -7, 2)
    monkeypatch.setattr(renderer, "measure_tmp_source_run", lambda *_args: measure)
    source_visual = visual_metrics(renderer, regular, "font", tmp_path / "font.ttf", 18, 1.0, source_metrics_only=True)
    assert source_visual.advance == 6

    monkeypatch.setattr(renderer_mod, "load_font", lambda *_args: object())
    monkeypatch.setattr(renderer, "measure_tmp_run", lambda *_args: measure)
    pillow_visual = visual_metrics(renderer, regular, "font", tmp_path / "font.ttf", 18, 1.0)
    assert pillow_visual.right == 5


def test_native_character_layout_advance_and_metric_modes(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    style = _style(voffset=2.0, mspace=10.0, scale_x=1.5)
    metrics = _metrics(width=6, height=8, bearing_x=1, bearing_y=7, advance=6)
    monkeypatch.setattr(renderer, "tmp_native_glyph_metrics", lambda *_args, **_kwargs: metrics)
    monkeypatch.setattr(renderer, "tmp_native_style_extents", lambda *_args: (9.0, -3.0))
    monkeypatch.setattr(renderer, "tmp_native_vertex_padding", lambda *_args: 1.0)
    monkeypatch.setattr(renderer, "tmp_native_character_sdf_scale", lambda *_args: 0.5)
    monkeypatch.setattr(renderer, "tmp_native_next_x_advance", lambda *_args: 20.0)

    info, next_x, ascender, descender, visible_count = renderer.tmp_native_layout_character(
        "A", style, "font", tmp_path / "font.ttf", 0, 0, 2.0, 1.0, 0, -999, 999, 0, "mesh", 1.0, 0.2
    )
    assert info.visible is True
    assert info.glyph_origin_x == 4.0
    assert info.sdf_scale == 0.5
    assert (next_x, visible_count) == (20.0, 1)
    assert ascender >= info.adjusted_ascender
    assert descender <= info.adjusted_descender

    space, *_ = renderer.tmp_native_layout_character(
        " ",
        replace(style, mspace=None),
        "font",
        tmp_path / "font.ttf",
        0,
        1,
        20,
        1,
        0,
        ascender,
        descender,
        1,
        "mesh",
        1.0,
        0.2,
    )
    assert space.visible is False
    assert space.ascender == ascender - 1

    renderer.tmp_font_library = SimpleNamespace(tab_advance=lambda *_args: 8.0)
    next_advance = renderer_mod.PNGRenderer.tmp_native_next_x_advance
    assert next_advance(renderer, 8, "\t", style, "font", metrics, 1.0, 1.0) == 16
    assert next_advance(renderer, 9, "\t", style, "font", metrics, 1.0, 1.0) == 16
    assert next_advance(renderer, 2, "A", style, "font", metrics, 1.0, 1.0) == 12
    normal_style = replace(style, mspace=None)
    monkeypatch.setattr(renderer, "tmp_character_spacing_advance", lambda *_args: 2.0)
    assert next_advance(renderer, 2, "A", normal_style, "font", metrics, 1.5, 1.0) == 13

    renderer.tmp_font_library.tab_advance = lambda *_args: None
    assert next_advance(renderer, 0, "\t", normal_style, "font", metrics, 1.0, 1.0) > 0

    glyph_metrics = renderer_mod.PNGRenderer.tmp_native_glyph_metrics
    line_break = glyph_metrics(renderer, "font", tmp_path / "font.ttf", "\n", normal_style)
    assert line_break.width == 0
    renderer.tmp_font_library.source_glyph_metrics = lambda *_args, **_kwargs: metrics
    monkeypatch.setattr(renderer, "tmp_render_glyph_char", lambda *_args: "A")
    assert (
        glyph_metrics(renderer, "font", tmp_path / "font.ttf", "A", normal_style, source_metrics_only=True) is metrics
    )
    renderer.tmp_font_library.source_glyph_metrics = lambda *_args, **_kwargs: None
    with pytest.raises(ValueError, match="source font metrics"):
        glyph_metrics(renderer, "font", tmp_path / "font.ttf", "A", normal_style, source_metrics_only=True)

    fake_font = object()
    monkeypatch.setattr(renderer_mod, "load_font", lambda *_args: fake_font)
    monkeypatch.setattr(renderer, "glyph_layout_metrics", lambda font, *_args: metrics if font is fake_font else None)
    assert glyph_metrics(renderer, "font", tmp_path / "font.ttf", "A", normal_style) is metrics


def test_native_run_advance_spacing_and_percent_indent_helpers(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    style = _style(cspace=2.0, bold=True)
    run = TextRun("AB", style)
    metrics = _metrics(advance=5)
    monkeypatch.setattr(renderer, "tmp_character_spacing_advance", lambda *_args: 2.0)
    monkeypatch.setattr(renderer, "tmp_native_glyph_metrics", lambda *_args, **_kwargs: metrics)
    monkeypatch.setattr(renderer, "use_em_block", lambda _run: False)
    assert renderer.tmp_native_run_advance(run, "font", tmp_path / "font.ttf", 1.0, 1.0) == 12.0

    monkeypatch.setattr(renderer, "use_em_block", lambda _run: True)
    monkeypatch.setattr(renderer, "tmp_source_block_metrics", lambda *_args: _metrics(advance=7))
    assert renderer.tmp_native_run_advance(run, "font", tmp_path / "font.ttf", 1.0, 1.5) == 23.0
    assert renderer.tmp_native_run_advance(TextRun("", style), "font", tmp_path / "font.ttf", 1.0, 1.5) == 10.5
    monkeypatch.setattr(renderer, "tmp_source_block_metrics", lambda *_args: None)
    assert renderer.tmp_native_run_advance(TextRun("A", style), "font", tmp_path / "font.ttf", 1.0, 1.0) == 36

    monkeypatch.setattr(renderer, "tmp_normal_spacing_advance", lambda *_args: 1.0)
    monkeypatch.setattr(renderer, "tmp_bold_spacing_advance", lambda *_args: 3.0)
    monkeypatch.setattr(renderer, "tmp_cspace_advance", lambda value: value)
    assert renderer.tmp_inter_run_spacing_advance(style, replace(style), "font", 18) == 6.0
    assert renderer.tmp_inter_run_spacing_advance(style, replace(style, cspace=0), "font", 18) == 4.0
    assert renderer.tmp_closes_cspace_before_next_run(style, replace(style, cspace=0)) is True
    assert renderer.tmp_closes_cspace_before_next_run(replace(style, cspace=0), style) is False

    percent_style = replace(style, indent=3, line_indent=2, indent_percent=0.1, line_indent_percent=0.2)
    line = StyledLine([run], percent_style)
    assert renderer.tmp_native_line_initial_x(line, 100) == pytest.approx(35)
    assert renderer.tmp_line_indent_percent(line) == pytest.approx(0.3)
    assert renderer.tmp_lines_have_percent_indent([line]) is True
    assert renderer.tmp_lines_have_percent_indent([StyledLine([], _style())]) is False
    assert renderer.tmp_native_visible_character("A") is True
    assert renderer.tmp_native_visible_character("\t") is True
    assert renderer.tmp_native_visible_character(" ") is False
    assert renderer.tmp_native_current_em_scale(25) == 0.25


def test_native_font_extents_preferred_sizes_and_percent_margin_resolution(tmp_path: Path, monkeypatch):
    renderer = _renderer(tmp_path)
    asset = _asset(tmp_path, point_size=20.0, face_scale=2.0, ascent_line=12.0, descent_line=-4.0, line_height=18.0)
    renderer.tmp_font_library = SimpleNamespace(active_asset=lambda _name: asset)
    style = _style(size=10)
    assert renderer.tmp_native_element_scale("font", 10) == 1.0
    assert renderer.tmp_native_style_extents("font", style) == (12.0, -4.0)
    assert renderer.tmp_native_raw_line_gap("font") == 2.0

    renderer.tmp_font_library.active_asset = lambda _name: None
    assert renderer.tmp_native_element_scale("font", 10) > 0
    assert renderer.tmp_native_style_extents("font", style) == (18.0, -2.0)
    assert renderer.tmp_native_raw_line_gap("font") == 0.0
    assert renderer.tmp_preferred_width(-1) == 0.01
    assert renderer.tmp_preferred_height(10, 10) == 10
    assert renderer.tmp_preferred_height(10.123) == 10.13

    base_layout = SimpleNamespace(
        preferred_width=40.0,
        content_height=20.0,
        dominant_size=18.0,
        lines=[
            SimpleNamespace(styled_line=StyledLine([], _style()), width=60.0),
            SimpleNamespace(styled_line=StyledLine([], replace(_style(), indent_percent=0.5)), width=50.0),
            SimpleNamespace(styled_line=StyledLine([], replace(_style(), indent_percent=1.0)), width=1.0),
        ],
    )
    renderer.tmp_preferred_padding_x = 2.0
    assert (
        renderer.tmp_preferred_percent_indent_margin_width(base_layout)
        == renderer_mod.TMP_PERCENT_INDENT_MAX_MARGIN_WIDTH
    )
    assert (
        renderer.tmp_resolve_percent_indent_margin_width(
            [StyledLine([], _style())], "font", tmp_path / "font.ttf", 18, 0, 18, 0, base_layout
        )
        is None
    )

    percent_lines = [StyledLine([], replace(_style(), indent_percent=0.2))]
    renderer.tmp_box_mode = "preferred"
    assert (
        renderer.tmp_resolve_percent_indent_margin_width(
            percent_lines, "font", tmp_path / "font.ttf", 18, 0, 18, 0, base_layout
        )
        == renderer_mod.TMP_PERCENT_INDENT_MAX_MARGIN_WIDTH
    )

    renderer.tmp_box_mode = "fixed"
    renderer.tmp_box_width = 100
    monkeypatch.setattr(renderer, "tmp_native_text_layout", lambda *_args, **_kwargs: None)
    assert (
        renderer.tmp_iterative_percent_indent_margin_width(
            percent_lines, "font", tmp_path / "font.ttf", 18, 0, 18, 0, base_layout
        )
        == 100
    )

    converged = SimpleNamespace(dominant_size=18.0, preferred_width=100.0, content_height=20.0)
    monkeypatch.setattr(renderer, "tmp_native_text_layout", lambda *_args, **_kwargs: converged)
    assert (
        renderer.tmp_iterative_percent_indent_margin_width(
            percent_lines, "font", tmp_path / "font.ttf", 18, 0, 18, 0, base_layout
        )
        == 100
    )
