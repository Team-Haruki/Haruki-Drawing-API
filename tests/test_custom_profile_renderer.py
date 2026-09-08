from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageFont
import pytest

from src.sekai.profile.custom_profile import drawer as custom_profile_drawer, renderer as renderer_mod
from src.sekai.profile.custom_profile.card_prefab import CardAlphaMaskOp, CardCoverArtOp
from src.sekai.profile.custom_profile.drawer import _optional_region_file, _region_path_candidates, _require_region_path
from src.sekai.profile.custom_profile.general_prefab import (
    GeneralTextOp,
    GeneralViewportOp,
    PillowGeneralPrefabAdapter,
    build_general_prefab_display_list,
)
from src.sekai.profile.custom_profile.limits import RasterSizeLimitError
from src.sekai.profile.custom_profile.renderer import (
    CHARA_LIST,
    GENERAL_NATIVE_SIZES,
    GENERAL_PREFAB_PALETTE,
    NativeContent,
    NativeUnresolvedContent,
    PNGRenderer,
    PreparedLayer,
    RenderedLayer,
    StyledLine,
    TMPDynamicFontField,
    TMPDynamicGlyphSDF,
    TMPFontLibrary,
    TMPGlyphMetrics,
    TMPNativeCharacterInfo,
    TMPStaticAtlasField,
    _TMPGlyphContourBuilder,
    build_arg_parser,
    harden_rgba_alpha,
    resize_rgba_premul,
)
from src.sekai.profile.custom_profile.split import decode_custom_profile_render_request
from src.sekai.profile.custom_profile.svg import TextRun, TextStyle, parse_tmp_text


def _base_tmp_style() -> TextStyle:
    return TextStyle(
        color="#000000",
        alpha=1.0,
        size=24.0,
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


def test_custom_profile_scale_tag_uses_first_tmp_attribute_value() -> None:
    tokens = parse_tmp_text("<scale=3 4><size=300><#9a4d3b>●", _base_tmp_style())
    runs = [token for token in tokens if isinstance(token, TextRun)]

    assert len(runs) == 1
    assert runs[0].text == "●"
    assert runs[0].style.scale_x == 3.0
    assert runs[0].style.size == 300.0
    assert runs[0].style.color == "#9a4d3b"


def test_custom_profile_tmp_parser_tolerates_real_profile_tag_typos() -> None:
    tokens = parse_tmp_text("<scale=1.8.><#FDECEI><pos=35><alpha=61#>●", _base_tmp_style())
    runs = [token for token in tokens if isinstance(token, TextRun)]

    assert len(runs) == 1
    assert runs[0].text == "●"
    assert runs[0].style.scale_x == 1.8
    assert runs[0].style.color == "#fdecef"
    assert runs[0].style.pos == 35.0
    assert 0.37 < runs[0].style.alpha < 0.39


def test_custom_profile_tmp_parser_consumes_pos_tag_between_symbols() -> None:
    tokens = parse_tmp_text("<size=80><scale=0.7><#D56844>▲<pos=35><voffset=-14>▲", _base_tmp_style())
    runs = [token for token in tokens if isinstance(token, TextRun)]

    assert "".join(run.text for run in runs) == "▲▲"
    assert not any("<" in run.text or ">" in run.text for run in runs)


def test_custom_profile_tmp_parser_tolerates_o_in_hex_color() -> None:
    tokens = parse_tmp_text("<size=56><#FOBDBA><scale=6>●", _base_tmp_style())
    runs = [token for token in tokens if isinstance(token, TextRun)]

    assert len(runs) == 1
    assert runs[0].text == "●"
    assert runs[0].style.color == "#ffbdba"


def test_custom_profile_native_text_layout_keeps_runs_breaks_and_empty_lines(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path)
    base_style = _base_tmp_style()
    spaced_style = replace(base_style, cspace=2.0)
    lines = [
        StyledLine([TextRun("A\n", spaced_style), TextRun("B", base_style)], base_style, trailing_newline_count=2),
        StyledLine([TextRun("", base_style)], base_style),
    ]
    metrics = TMPGlyphMetrics(1.0, 1.0, 0.0, 1.0, 1.0, 0, 0, 1, 1, 1.0, 0)

    monkeypatch.setattr(renderer, "tmp_native_element_scale", lambda *_: 1.0)
    monkeypatch.setattr(renderer, "tmp_native_current_em_scale", lambda *_: 1.0)
    monkeypatch.setattr(renderer, "tmp_native_raw_line_gap", lambda *_: 0.0)
    monkeypatch.setattr(renderer, "tmp_native_line_initial_x", lambda *_: 0.0)
    monkeypatch.setattr(renderer, "tmp_closes_cspace_before_next_run", lambda style, _next: style.cspace > 0.0)
    monkeypatch.setattr(renderer, "tmp_cspace_advance", lambda cspace: cspace)
    monkeypatch.setattr(renderer, "tmp_native_style_extents", lambda *_: (6.0, -2.0))
    monkeypatch.setattr(renderer, "tmp_preferred_width", lambda width: width)
    monkeypatch.setattr(renderer, "tmp_preferred_height", lambda height, _face_height: height)
    monkeypatch.setattr(renderer.tmp_font_library, "active_asset", lambda *_: None)
    monkeypatch.setattr(
        renderer,
        "tmp_native_measure_line_runs",
        lambda line, *_args, **_kwargs: (
            [(run, 0.0, float(len(run.text) * 10)) for run in line.runs],
            float(sum(len(run.text.replace("\n", "")) for run in line.runs) * 10),
            0.0,
            20.0,
            24.0,
        ),
    )

    def fake_character(
        char,
        style,
        _font_name,
        _font_path,
        line_index,
        index,
        x_advance,
        line_offset,
        _first_character_index,
        max_ascender,
        max_descender,
        visible_count,
        *_args,
        **_kwargs,
    ):
        next_advance = x_advance + 10.0
        ascender = 9.0 if char == "\n" else 8.0
        visible = char != "\n"
        info = TMPNativeCharacterInfo(
            index=index,
            char=char,
            line_index=line_index,
            x_origin=x_advance,
            x_advance=next_advance,
            glyph_origin_x=x_advance,
            bottom_left_x=x_advance,
            bottom_left_y=-2.0 - line_offset,
            top_left_x=x_advance,
            top_left_y=ascender - line_offset,
            top_right_x=next_advance,
            top_right_y=ascender - line_offset,
            bottom_right_x=next_advance,
            bottom_right_y=-2.0 - line_offset,
            vertex_padding=0.0,
            raw_left_x=x_advance,
            raw_right_x=next_advance,
            raw_top_y=ascender - line_offset,
            raw_bottom_y=-2.0 - line_offset,
            baseline=-line_offset,
            ascender=ascender - line_offset,
            descender=-2.0 - line_offset,
            adjusted_ascender=ascender,
            adjusted_descender=-2.0,
            visible=visible,
            style=style,
            metrics=metrics,
            sdf_scale=1.0,
        )
        return info, next_advance, max(max_ascender, ascender), min(max_descender, -2.0), visible_count + visible

    monkeypatch.setattr(renderer, "tmp_native_layout_character", fake_character)

    layout = renderer.tmp_native_text_layout(lines, "font", tmp_path / "font.ttf", 24.0, 1.0, 24.0)

    assert layout is not None
    assert [character.char for character in layout.characters] == ["A", "B", "\n", "\n"]
    assert layout.characters[0].x_advance == 8.0
    assert [line.visible_character_count for line in layout.lines] == [2, 0]
    assert [(line.first_character_index, line.last_character_index) for line in layout.lines] == [(0, 3), (4, 4)]
    assert layout.lines[0].baseline == 0.0
    assert layout.lines[1].baseline < 0.0


def _write_png(path: Path, size: tuple[int, int] = (3, 2)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (255, 0, 0, 255)).save(path)


def _write_png_color(path: Path, size: tuple[int, int], color: tuple[int, int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, color).save(path)


def _image_has_content_in_box(image: Image.Image, box: tuple[int, int, int, int]) -> bool:
    return image.crop(box).getchannel("A").getbbox() is not None


def _make_renderer(
    tmp_path: Path,
    *,
    profile_context: dict | None = None,
    resources: dict | None = None,
    region: str = "cn",
    **renderer_kwargs: object,
) -> PNGRenderer:
    fonts = tmp_path / "fonts"
    assets = tmp_path / "asset" / f"{region}-assets" / "startapp" / "custom_profile"
    fonts.mkdir(exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    return PNGRenderer(
        masterdata=None,
        assets=assets,
        fonts=fonts,
        resources=resources or {},
        tmp_font_metadata=None,
        shape_sprite_dir=None,
        unity_ui_sprite_dir=None,
        profile_context=profile_context or {},
        region=region,
        **renderer_kwargs,
    )


class _MetricsFont:
    def __init__(self) -> None:
        self.bboxes = {
            " ": (0, 3, 3, 8),
            "  ": (0, 3, 6, 8),
            "A": (-1, 2, 5, 9),
            "B": (0, 1, 4, 10),
            "AB": (-1, 1, 9, 10),
            "A B": (-1, 1, 12, 10),
        }
        self.lengths = {" ": 3.0, "A": 5.0, "B": 4.0}

    def getbbox(self, text: str) -> tuple[int, int, int, int]:
        return self.bboxes[text]

    def getlength(self, text: str) -> float:
        return self.lengths[text]


def _glyph_metrics(
    *,
    width: float = 4.0,
    height: float = 6.0,
    bearing_x: float = -1.0,
    bearing_y: float = 5.0,
    advance: float = 5.0,
) -> renderer_mod.TMPGlyphMetrics:
    return renderer_mod.TMPGlyphMetrics(
        width=width,
        height=height,
        bearing_x=bearing_x,
        bearing_y=bearing_y,
        advance=advance,
        rect_x=0,
        rect_y=0,
        rect_w=0,
        rect_h=0,
        glyph_scale=1.0,
        atlas_index=0,
    )


def test_custom_profile_text_bbox_scales_spaces_and_keeps_visual_bounds(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path, tmp_space_width_factor=2.0)
    font = _MetricsFont()

    assert renderer.text_bbox(font, "AB") == (-1, 1, 9, 10)
    assert renderer.text_bbox(font, "A B") == (-1, 1, 15, 10)
    assert renderer.text_bbox(font, "  ") == (0, 3, 12, 8)


def test_custom_profile_glyph_advance_uses_tab_dynamic_static_and_pillow_metrics(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path, tmp_metrics_mode="asset-fallback", tmp_space_width_factor=2.0)
    font = _MetricsFont()
    dynamic = SimpleNamespace(atlas_population_mode=1)
    static = SimpleNamespace(atlas_population_mode=0)
    calls: list[tuple[str, bool]] = []
    renderer.tmp_render_glyph_char = lambda _font, ch, _size: ch  # type: ignore[method-assign]
    renderer.tmp_font_library = SimpleNamespace(
        tab_advance=lambda *_args: 13.0,
        active_asset=lambda _name: dynamic,
        glyph_metrics=lambda *_args, **_kwargs: pytest.fail("dynamic glyph table must not be used"),
        source_glyph_metrics=lambda _name, ch, _size, *, include_fallback: (
            calls.append((ch, include_fallback)) or _glyph_metrics(advance=7.0)
        ),
    )

    assert renderer.glyph_advance(font, "\t", "Rodin", 24.0) == 13.0
    assert renderer.glyph_advance(font, "A", "Rodin", 24.0) == 7.0
    assert calls == [("A", True)]

    renderer.tmp_font_library.active_asset = lambda _name: static
    renderer.tmp_font_library.glyph_metrics = lambda *_args, **_kwargs: _glyph_metrics(advance=8.0)
    assert renderer.glyph_layout_metrics_with_source(font, "A", "Rodin", 24.0)[1] == "tmp-character-table"
    assert renderer.glyph_advance(font, "A", "Rodin", 24.0) == 8.0

    renderer.tmp_font_library.glyph_metrics = lambda *_args, **_kwargs: None
    renderer.tmp_font_library.source_glyph_metrics = lambda *_args, **_kwargs: _glyph_metrics(advance=9.0)
    metrics, source = renderer.glyph_layout_metrics_with_source(font, "A", "Rodin", 24.0)
    assert (metrics.advance, source) == (9.0, "source-font-fallback")

    renderer.tmp_metrics_mode = "pil"
    assert renderer.glyph_advance(font, " ", "Rodin", 24.0) == 6.0


def test_custom_profile_run_measurement_preserves_spacing_bounds_and_empty_runs(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)
    style = _base_tmp_style()
    run = TextRun("AB", style)
    metrics = {
        "A": _glyph_metrics(),
        "B": _glyph_metrics(width=3.0, height=4.0, bearing_x=1.0, bearing_y=3.0, advance=4.0),
        " ": _glyph_metrics(width=0.0, height=0.0, bearing_x=0.0, bearing_y=0.0, advance=3.0),
    }
    renderer.tmp_native_visible_character = lambda ch: not ch.isspace()  # type: ignore[method-assign]
    renderer.tmp_character_spacing_advance = lambda *_args: 1.5  # type: ignore[method-assign]
    renderer.glyph_layout_metrics = lambda _font, ch, _name, _size: metrics[ch]  # type: ignore[method-assign]
    renderer.tmp_render_glyph_char = lambda _font, ch, _size: ch  # type: ignore[method-assign]
    renderer.tmp_font_library.source_glyph_metrics = lambda _name, ch, _size, **_kwargs: metrics.get(ch)

    expected = renderer_mod.TMPRunMeasure(10.5, -1.0, 10.5, -5.0, 1.0)
    assert renderer.measure_tmp_run(_MetricsFont(), run, "Rodin", 24.0) == expected
    assert renderer.measure_tmp_source_run(run, "Rodin", 24.0) == expected

    mono_run = TextRun("AB", replace(style, mspace=8.0))
    renderer.tmp_mspace_advance = lambda _value: 8.0  # type: ignore[method-assign]
    assert renderer.measure_tmp_run(_MetricsFont(), mono_run, "Rodin", 24.0) == renderer_mod.TMPRunMeasure(
        17.5, 0.5, 15.5, -5.0, 1.0
    )

    empty = TextRun("", style)
    assert renderer.measure_tmp_run(_MetricsFont(), empty, "Rodin", 24.0) == renderer_mod.TMPRunMeasure(
        3.0, 0.0, 3.0, 0.0, 0.0
    )
    renderer.tmp_font_library.source_glyph_metrics = lambda *_args, **_kwargs: None
    with pytest.raises(ValueError, match="U\\+0041"):
        renderer.measure_tmp_source_run(TextRun("A", style), "Rodin", 24.0)


def test_custom_profile_run_bboxes_preserve_plain_fx_and_empty_geometry(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)
    font = _MetricsFont()
    run = TextRun("AB", replace(_base_tmp_style(), scale_x=2.0))
    renderer.tmp_render_glyph_char = lambda _font, ch, _size: ch  # type: ignore[method-assign]
    renderer.tmp_native_visible_character = lambda _ch: True  # type: ignore[method-assign]
    renderer.glyph_advance = lambda _font, ch, *_args: font.getlength(ch)  # type: ignore[method-assign]
    renderer.tmp_character_spacing_advance = lambda *_args: 1.0  # type: ignore[method-assign]
    renderer.tmp_fx_scale_x = lambda _style: 2.0  # type: ignore[method-assign]
    renderer.tmp_fx_advance_scale_x = lambda _style: 1.5  # type: ignore[method-assign]

    assert renderer.run_bbox(font, run, "Rodin", 24.0) == (-1, 1, 10, 10)
    assert renderer.run_fx_bbox(font, run, "Rodin", 24.0) == (-4, 1, 15, 10)
    assert renderer.run_bbox(font, TextRun("", run.style), "Rodin", 24.0) == (0, 3, 3, 8)


def test_custom_profile_renderer_value_helpers_preserve_legacy_fallbacks(tmp_path: Path) -> None:
    configured: dict[str, object] = {"value": 1}

    assert renderer_mod._mapping_or_empty(configured) is configured
    assert renderer_mod._mapping_or_empty([]) == {}
    assert renderer_mod._optional_dict(configured) is configured
    assert renderer_mod._optional_dict(None) == {}
    assert renderer_mod._default_if_none(None, 3) == 3
    assert renderer_mod._default_if_none(0, 3) == 0
    assert renderer_mod._choice_or_default("full", {"full"}, "serial") == "full"
    assert renderer_mod._choice_or_default("bad", {"full"}, "serial") == "serial"
    assert renderer_mod._positive_int(0) == 1
    assert renderer_mod._positive_int(-2) == 1
    assert renderer_mod._positive_float(0.0) == 1.0
    assert renderer_mod._positive_float(2.5) == 2.5
    assert renderer_mod._game_assets_root(tmp_path / "custom_profile") == tmp_path
    assert renderer_mod._game_assets_root(tmp_path / "other") == tmp_path / "other"
    assert renderer_mod._first_truthy(0, "", 4, default=9) == 4
    assert renderer_mod._first_truthy(0, "", default=9) == 9
    assert renderer_mod._float_first(0, "2.5") == 2.5
    assert renderer_mod._int_first(None, "4") == 4
    assert renderer_mod._nonempty_strings([None, "", "Font"]) == ["None", "Font"]
    assert renderer_mod._record_or_noop(None)(tmp_path) is None


def test_tmp_font_library_loads_metadata_with_material_and_glyph_fallbacks(tmp_path: Path) -> None:
    metadata_path = tmp_path / "fonts.json"
    source_font = tmp_path / "font.ttf"
    atlas = tmp_path / "atlases" / "font_77.png"
    chars_path = tmp_path / "chars.json"
    glyphs_path = tmp_path / "glyphs.json"
    source_font.write_bytes(b"font")
    _write_png(atlas)
    chars_path.write_text(json.dumps([{"m_Unicode": 65, "m_GlyphIndex": 3, "m_Scale": 0}]), encoding="utf-8")
    glyphs_path.write_text(
        json.dumps(
            [
                {
                    "m_Index": 3,
                    "m_Scale": 2,
                    "m_AtlasIndex": 1,
                    "m_Metrics": {
                        "m_Width": 8,
                        "m_Height": 9,
                        "m_HorizontalBearingX": 1,
                        "m_HorizontalBearingY": 7,
                        "m_HorizontalAdvance": 10,
                    },
                    "m_GlyphRect": {"m_X": 2, "m_Y": 3, "m_Width": 8, "m_Height": 9},
                }
            ]
        ),
        encoding="utf-8",
    )
    common = {
        "name": "Rodin",
        "material": "material-1",
        "source_font_data_path": "font.ttf",
        "atlas_textures": [77],
        "character_table_path": "chars.json",
        "glyph_table_path": "glyphs.json",
        "atlas_population_mode": "2",
        "atlas_width": 0,
        "atlas_padding": 0,
        "face_info": {"m_PointSize": 0, "m_Scale": 0, "m_LineHeight": 12},
        "creation_settings": {"pointSize": 24},
        "fallback_font_asset_names": [None, "", "Fallback"],
    }
    metadata_path.write_text(
        json.dumps(
            {
                "materials": [
                    {"path_id": "material-1", "floats": {"_TextureWidth": 64, "_GradientScale": 0}},
                    {"floats": {}},
                ],
                "tmp_font_assets": [
                    {**common, "bundle": "secondary.bundle"},
                    {**common, "bundle": "custom_profile_font.bundle"},
                ],
            }
        ),
        encoding="utf-8",
    )
    recorded: list[Path] = []

    assets = TMPFontLibrary._load_assets(metadata_path, recorded.append)

    assert [asset.bundle for asset in assets["Rodin"]] == ["custom_profile_font.bundle", "secondary.bundle"]
    asset = assets["Rodin"][0]
    assert asset.source_font_path == source_font
    assert asset.atlas_paths == [atlas]
    assert asset.atlas_population_mode == 2
    assert asset.atlas_width == 64.0
    assert asset.atlas_padding == 5.0
    assert asset.gradient_scale == 6.0
    assert asset.point_size == 24.0
    assert asset.face_scale == 1.0
    assert asset.fallback_names == ["None", "Fallback"]
    assert asset.glyphs[65].advance == 10.0
    assert asset.glyphs[65].glyph_scale == 2.0
    assert metadata_path in recorded
    assert chars_path in recorded
    assert glyphs_path in recorded
    assert tmp_path / "atlases" in recorded


def test_tmp_font_library_character_tables_fail_open_when_inputs_are_missing(tmp_path: Path) -> None:
    assert TMPFontLibrary._load_character_table(tmp_path, {}) == {}
    assert (
        TMPFontLibrary._load_character_table(
            tmp_path,
            {"character_table_path": "missing-chars.json", "glyph_table_path": "missing-glyphs.json"},
        )
        == {}
    )


def test_custom_profile_resource_index_accepts_wrapped_mapping_and_list_shapes(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)
    keyed = {"7": {"name": "fallback-key"}, "ignored": "text", "bad": {"id": "not-int"}}
    wrapped = {"items": [{"id": "8", "name": "wrapped"}, "ignored"]}
    listed = [{"id": 9, "name": "listed"}, {"name": "missing-id"}, None]

    assert renderer.coerce_resource_index(keyed) == {7: keyed["7"]}
    assert renderer.coerce_resource_index(wrapped) == {8: wrapped["items"][0]}
    assert renderer.coerce_resource_index(listed) == {9: listed[0]}
    assert renderer.coerce_resource_index("invalid") == {}
    assert renderer_mod._resource_entries({"items": "not-a-list", "10": {"id": 10}})[0][0] == "items"
    assert renderer_mod._coerced_resource_entry(None, "invalid") is None


def test_custom_profile_request_asset_candidates_cover_supported_prefixes(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)
    renderer.static_images = tmp_path / "static_images"
    renderer.data_root_candidates = lambda: [tmp_path]  # type: ignore[method-assign]
    absolute = tmp_path / "absolute.png"

    assert renderer.request_asset_candidates(None) == []
    assert renderer.request_asset_candidates(str(absolute)) == [absolute]
    assert renderer.request_asset_candidates("asset/cn-assets/a.png") == [
        tmp_path / "asset/cn-assets/a.png",
        tmp_path / "cn-assets/a.png",
    ]
    assert renderer.request_asset_candidates("cn-assets/a.png") == [
        tmp_path / "asset/cn-assets/a.png",
        tmp_path / "cn-assets/a.png",
    ]
    assert renderer.request_asset_candidates("static_images/a.png") == [tmp_path / "static_images/a.png"]
    ordinary = renderer.request_asset_candidates("folder/a.png")
    assert ordinary[0] == Path("folder/a.png")
    assert len(ordinary) == len(set(ordinary))
    assert renderer_mod._dedupe_paths([absolute, absolute]) == [absolute]


def test_custom_profile_resource_path_covers_masterdata_layout_variants(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)
    renderer.masterdata = tmp_path
    resource_files = {
        "nested": renderer.assets / "bg" / "nested.png",
        "root": renderer.assets / "root.png",
        "fallback": renderer.assets / "shape" / "fallback.png",
        "plain": renderer.assets / "misc" / "plain.png",
    }
    for path in resource_files.values():
        _write_png(path)

    assert (
        renderer.resource_path({"resourceLoadVal": "custom_profile/bg", "fileName": "nested"})
        == resource_files["nested"]
    )
    assert (
        renderer.resource_path({"resourceLoadVal": "custom_profile", "fileName": "root.png"}) == resource_files["root"]
    )
    assert (
        renderer.resource_path({"resourceLoadVal": "ignored", "fileName": "fallback"}, "shape")
        == resource_files["fallback"]
    )
    assert renderer.resource_path({"resourceLoadVal": "misc", "fileName": "plain"}) == resource_files["plain"]
    assert renderer.resource_path({"resourceLoadVal": "misc"}) is None
    assert renderer_mod._png_resource_filename({"fileName": ""}) is None

    renderer.masterdata = None
    assert renderer.resource_path({"resourceLoadVal": "misc", "fileName": "plain"}) is None


def test_custom_profile_renderer_normalizes_invalid_constructor_options(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        parallel_workers=0,
        parallel_stage="invalid",
        tmp_text_render_mode="invalid",
        shape_sdf_source="invalid",
        tmp_metrics_mode="invalid",
        max_layer_pixels=-1,
        max_scene_bytes=-1,
        tmp_decorative_alpha_harden=0,
        position_scale=2.0,
        position_scale_x=3.0,
    )

    assert renderer.parallel_workers == 1
    assert renderer.parallel_stage == "transform"
    assert renderer.tmp_text_render_mode == renderer_mod.DEFAULT_TMP_TEXT_RENDER_MODE
    assert renderer.shape_sdf_source == "rgb"
    assert renderer.tmp_metrics_mode == "pil"
    assert renderer.max_layer_pixels == 1
    assert renderer.max_scene_bytes == 1
    assert renderer.tmp_decorative_alpha_harden == 1.0
    assert renderer.position_scale == 2.0
    assert renderer.position_scale_x == 3.0
    assert renderer.position_scale_y == 2.0


def test_custom_profile_oversized_scaled_shape_rasters_only_visible_region(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(
        tmp_path,
        canvas_w=64,
        canvas_h=32,
        origin_x=32,
        origin_y=16,
        position_scale_x=1.0,
        position_scale_y=1.0,
        max_layer_pixels=8_388_608,
    )
    shape_path = tmp_path / "square.png"
    shape_path.touch()
    source_mask = Image.new("L", (499, 317), 255)
    renderer.shapes = {1: {"fileName": "square"}}
    monkeypatch.setattr(renderer, "shape_resource_path", lambda _resource: shape_path)
    monkeypatch.setattr(renderer, "shape_alpha_mask", lambda *_args: source_mask)
    captured: dict[str, object] = {}

    def fake_render_distance_field_shape(
        _path,
        _resource_file,
        _fill_color,
        _fill_alpha,
        _outline_color,
        _outline_alpha,
        _outline_size,
        output_size=None,
        output_bounds=None,
    ):
        captured["output_size"] = output_size
        captured["output_bounds"] = output_bounds
        left, top, right, bottom = output_bounds
        return Image.new("RGBA", (right - left, bottom - top), (255, 0, 0, 255))

    monkeypatch.setattr(renderer, "render_distance_field_shape", fake_render_distance_field_shape)
    object_data = {
        "visible": True,
        "position": {"x": 0, "y": 0},
        "scale": {"x": 9.0, "y": 6.0},
        "rotation": {"x": 0, "y": 0, "z": 0, "w": 1},
    }
    result = renderer.render_shape(
        {
            "id": 1,
            "alpha": 1.0,
            "outlineAlpha": 0.0,
            "outlineSize": 0.0,
            "objectData": object_data,
        }
    )

    assert result is not None
    assert captured["output_size"] == (4_491, 1_902)
    bounds = captured["output_bounds"]
    assert isinstance(bounds, tuple)
    assert (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]) < 8_388_608
    assert result[2] is True

    prepared = renderer.prepare_transformed_layer(result, object_data, "shape")
    assert prepared is not None
    assert prepared.xy[0] <= 0
    assert prepared.xy[1] <= 0
    assert prepared.xy[0] + prepared.image.width >= renderer.canvas_w
    assert prepared.xy[1] + prepared.image.height >= renderer.canvas_h


def test_custom_profile_clipped_scaled_shape_preserves_full_layer_geometry(tmp_path: Path, monkeypatch) -> None:
    shape_path = tmp_path / "square.png"
    _write_png_color(shape_path, (40, 30), (255, 0, 0, 255))
    renderers = [
        _make_renderer(
            tmp_path,
            canvas_w=64,
            canvas_h=32,
            origin_x=32,
            origin_y=16,
            position_scale_x=1.0,
            position_scale_y=1.0,
            max_layer_pixels=max_pixels,
        )
        for max_pixels in (1_000_000, 50_000)
    ]
    for renderer in renderers:
        renderer.shapes = {1: {"fileName": "square"}}
        renderer.colors = {1: "#ff0000"}
        monkeypatch.setattr(renderer, "shape_resource_path", lambda _resource: shape_path)

    shape = {
        "id": 1,
        "colorId": 1,
        "alpha": 1.0,
        "outlineAlpha": 0.0,
        "outlineSize": 0.0,
        "objectData": {
            "visible": True,
            "layer": 1,
            "position": {"x": -202, "y": 0},
            "scale": {"x": 10.0, "y": 10.0},
            "rotation": {"x": 0, "y": 0, "z": 0.173648, "w": 0.984808},
        },
    }
    card = {"customProfileCard": {"shapes": [shape]}}

    full = renderers[0].render_card(card)
    clipped = renderers[1].render_card(card)

    assert clipped.tobytes() == full.tobytes()
    assert clipped.getpixel((10, 16))[:3] != (255, 255, 255)
    assert clipped.getpixel((50, 16)) == (255, 255, 255, 255)


def test_custom_profile_shape_region_samples_the_full_scaled_field(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path)
    shape_path = tmp_path / "shape.png"
    shape_path.touch()
    source = Image.new("RGBA", (8, 6))
    source.putdata(
        [
            ((x * 29 + y * 7) % 256, 0, 0, (x * 11 + y * 31) % 256)
            for y in range(source.height)
            for x in range(source.width)
        ]
    )
    monkeypatch.setattr(renderer, "shape_distance_field", lambda *_args: source.getchannel("R"))
    monkeypatch.setattr(renderer, "shape_alpha_mask", lambda *_args: source.getchannel("A"))
    output_size = (80, 60)
    bounds = (15, 10, 65, 50)

    full_field, full_alpha, full_fwidth = renderer.shape_shader_arrays(shape_path, "square", output_size)
    region_field, region_alpha, region_fwidth = renderer.shape_shader_arrays(
        shape_path,
        "square",
        output_size,
        bounds,
    )

    np.testing.assert_array_equal(region_field, full_field[bounds[1] : bounds[3], bounds[0] : bounds[2]])
    np.testing.assert_array_equal(region_alpha, full_alpha[bounds[1] : bounds[3], bounds[0] : bounds[2]])
    np.testing.assert_array_equal(
        region_fwidth[1:-1, 1:-1],
        full_fwidth[bounds[1] + 1 : bounds[3] - 1, bounds[0] + 1 : bounds[2] - 1],
    )


def test_custom_profile_general_text_helpers_split_ascii_tokens_and_long_runs(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)
    font = ImageFont.load_default()

    assert renderer_mod._general_text_tokens("alpha.test中!") == ["alpha.test", "中", "!"]
    lines: list[str] = []
    assert renderer_mod._append_general_token("cd", "ab", 3, len, lines) == "cd"
    assert lines == ["ab"]
    lines = []
    assert renderer_mod._append_general_token("abcdef", "", 3, len, lines) == "def"
    assert lines == ["abc"]
    assert renderer.wrap_general_text("", font, 20) == [""]
    assert renderer.wrap_general_text("alpha.test中", font, 1)


def test_custom_profile_general_prefab_asset_helpers_cover_supported_views(tmp_path: Path) -> None:
    challenge_icon = tmp_path / "challenge.png"
    story_image = tmp_path / "story.png"
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userChallengeLiveSoloResult": {"characterId": 5},
            "userStoryFavorites": [{"storyType": "unit", "storyId": 7}, "ignored"],
        },
    )
    renderer.chara_icon_path = lambda character_id: challenge_icon if character_id == 5 else None  # type: ignore[method-assign]
    renderer.story_favorite_image_path = lambda _story: story_image  # type: ignore[method-assign]

    assert renderer._general_prefab_asset_paths("ChallengeLive") == {"challenge_character_icon": challenge_icon}
    assert renderer._general_prefab_asset_paths("StoryFavorite") == {
        renderer_mod.story_favorite_asset_key({"storyType": "unit", "storyId": 7}): story_image
    }
    rank_assets = renderer._general_prefab_asset_paths("CharacterRankAndChallengeStage")
    assert len(rank_assets) == sum(character_id is not None for _name, character_id in renderer_mod.CHARA_LIST)
    assert renderer._general_prefab_asset_paths("X") == {}
    assert set(renderer._general_prefab_labels()) == {
        "comment_title",
        "total_power",
        "multi_live_title",
        "multi_live_count_suffix",
        "challenge_live_title",
        "challenge_live_solo",
        "character_rank_tab",
        "challenge_stage_tab",
        "music_clear",
        "music_full_combo",
        "music_all_perfect",
        "story_favorite_title",
        "not_set",
    }

    renderer.profile_context = {"userChallengeLiveSoloResult": "invalid", "userStoryFavorites": "invalid"}
    assert renderer._challenge_live_prefab_assets() == {"challenge_character_icon": None}
    assert renderer._story_favorite_prefab_assets() == {}


def test_custom_profile_honor_level_visual_chooses_exact_then_nearest_lower(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)
    level_one = {"level": 1, "assetbundleName": "one"}
    level_three = {"level": 3, "honorRarity": "high"}
    level_five = {"level": 5, "assetbundleName": "five"}
    honor = {"levels": [None, {}, level_one, level_three, level_five]}

    assert renderer.resolve_honor_level_visual(honor, 3) is level_three
    assert renderer.resolve_honor_level_visual(honor, 4) is level_three
    assert renderer.resolve_honor_level_visual(honor, 6) is level_five
    assert renderer.resolve_honor_level_visual(honor, 0) is level_one
    assert renderer.resolve_honor_level_visual({"levels": []}, 2) is None


def test_custom_profile_honor_metadata_helpers_preserve_fallback_order(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)
    honor = {"assetbundleName": "primary", "honorRarity": ""}
    visual = {"assetbundleName": "secondary", "honorRarity": "high", "level": 4}

    assert renderer._honor_asset_details(honor, visual, 0) == ("primary", "high", 4)
    assert renderer._honor_asset_details(honor, None, 2) == ("primary", "", 2)
    assert renderer._honor_background_asset_name({"backgroundAssetBundleName": "bg"}, "asset") == "bg"
    assert renderer._honor_background_asset_name({}, "asset") == "asset"
    assert renderer._resolved_honor_group_type({"honorType": "world_link"}, "", "") == "wl_event"
    assert renderer._honor_scroll_path("") is None
    assert renderer._honor_level_icon_paths("normal", "event") == (None, None)


def test_custom_profile_honor_frame_path_handles_birthday_and_rarity_rules(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)
    renderer.static_images = tmp_path / "static_images"
    static_frame = renderer.static_images / "honor" / "frame_degree_s_2.png"
    _write_png(static_frame)
    region_frame = tmp_path / "region-frame.png"
    requested_rels: list[Path] = []

    def first_region_asset(rels):
        requested_rels.extend(rels)
        return region_frame

    renderer.first_region_asset = first_region_asset  # type: ignore[method-assign]

    assert renderer.honor_frame_path({"honorType": "birthday"}, "honor_bg_birthday_miku", "", "sub", 1) is None
    assert renderer.honor_frame_path({"honorType": "birthday"}, "honor_bg_birthday_miku", "", "sub", 2) == region_frame
    assert Path("honor_frame/honor_frame_birthday_miku/frame_degree_s_2.png") in requested_rels

    requested_rels.clear()
    assert renderer.honor_frame_path({"frameName": "event_frame"}, "", "", "sub", 2) == static_frame
    assert requested_rels == []
    assert renderer.honor_frame_path({"frameName": "normal_frame"}, "", "", "sub", 2) == region_frame


def test_custom_profile_bonds_honor_helpers_resolve_slots_and_load_optional_images(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)
    keys = renderer._bonds_honor_request_keys(1, 2, False, 3, False, True)
    assert len(keys) == 2
    renderer.bonds_honor_requests = {keys[1]: {"imagePath": "second"}, "1": {"imagePath": "fallback"}}
    renderer.honor_request_image = lambda payload: payload and payload["imagePath"]  # type: ignore[method-assign]
    assert renderer._configured_bonds_honor_image(1, keys) == "second"
    assert renderer._configured_bonds_honor_image(1, ["missing"]) == "fallback"

    image_path = tmp_path / "loaded.png"
    _write_png(image_path)
    renderer.open_rgba = lambda path: Image.open(path).convert("RGBA")  # type: ignore[method-assign]
    request = SimpleNamespace(bonds_bg_path=str(image_path), frame_img_path=None)
    loaded = renderer._loaded_request_images(request, {"background": "bonds_bg_path", "frame": "frame_img_path"})
    assert loaded["background"] is not None
    assert loaded["frame"] is None


def test_custom_profile_card_asset_path_uses_kind_and_training_lookup_table(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)
    renderer.card_assets = {
        10: {
            "deckNormalPath": "deck-normal",
            "deckAfterTrainingPath": "deck-trained",
            "clipNormalPath": "clip-normal",
            "clipAfterTrainingPath": "clip-trained",
            "smallNormalPath": "small-normal",
            "smallAfterTrainingPath": "small-trained",
            "normalPath": "full-normal",
            "afterTrainingPath": "full-trained",
        }
    }
    renderer.resolve_request_asset_path = lambda raw: Path(raw) if raw else None  # type: ignore[method-assign]

    assert renderer.card_asset_path_for_state(10, False, "deck") == Path("deck-normal")
    assert renderer.card_asset_path_for_state(10, True, "deck") == Path("deck-trained")
    assert renderer.card_asset_path_for_state(10, False, "clip") == Path("deck-normal")
    assert renderer.card_asset_path_for_state(10, True, "clip") == Path("deck-trained")
    assert renderer.card_asset_path_for_state(10, False, "small") == Path("small-normal")
    assert renderer.card_asset_path_for_state(10, True, "small") == Path("small-trained")
    assert renderer.card_asset_path_for_state(10, False, "unknown") == Path("full-normal")
    assert renderer.card_asset_path_for_state(10, True) == Path("full-trained")
    assert renderer.card_asset_path_for_state(99, False) is None


def test_custom_profile_profile_level_helpers_accept_list_dict_and_fallback_rows(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userHonors": ["invalid", [1], [2, 4], {"honorId": 3, "honorLevel": 5}],
            "userProfileHonors": [{"honorId": 6, "honorLevel": 7}],
            "userBondsHonors": [[8, 9], {"bondsHonorId": 10, "bondsHonorLevel": 11}],
            "userHonorMissions": [{"honorId": 12, "missionProgress": 13}],
        },
    )

    assert renderer.user_honor_level_for(1) == 0
    assert renderer.user_honor_level_for(2) == 4
    assert renderer.user_honor_level_for(3) == 5
    assert renderer.user_honor_level_for(6) == 7
    assert renderer.user_honor_level_for(99) == 0
    assert renderer.user_bonds_honor_level_for(8) == 9
    assert renderer.user_bonds_honor_level_for(10) == 11
    assert renderer.user_bonds_honor_level_for(99) == 0
    assert renderer.user_honor_mission_progress_for(12) == 13
    assert renderer.user_honor_mission_progress_for(99) == 0


def test_masterdata_honor_request_builder_does_not_decode_images(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path)
    renderer.masterdata = tmp_path
    renderer.honors = {
        123: {
            "groupId": 7,
            "assetbundleName": "honor_asset",
            "honorRarity": "high",
        }
    }
    renderer.honor_groups = {
        7: {
            "honorType": "character",
            "backgroundAssetbundleName": "honor_background",
        }
    }
    expected_paths = {
        "honor": tmp_path / "degree_sub.png",
        "rank": tmp_path / "rank_sub.png",
        "frame": tmp_path / "frame_sub.png",
        "scroll": tmp_path / "scroll.png",
        "lv": tmp_path / "icon_degreeLv.png",
        "lv6": tmp_path / "icon_degreeLv6.png",
    }
    monkeypatch.setattr(renderer, "honor_background_path", lambda *_: expected_paths["honor"])
    monkeypatch.setattr(renderer, "honor_rank_path", lambda *_: expected_paths["rank"])
    monkeypatch.setattr(renderer, "honor_frame_path", lambda *_: expected_paths["frame"])
    monkeypatch.setattr(renderer, "first_region_asset", lambda *_: expected_paths["scroll"])
    monkeypatch.setattr(
        renderer,
        "static_image_path",
        lambda *parts: expected_paths["lv6" if "Lv6" in parts[-1] else "lv"],
    )
    monkeypatch.setattr(
        renderer,
        "open_rgba",
        lambda *_: pytest.fail("request derivation must not decode an image"),
    )

    request = renderer.build_masterdata_honor_request(123, 4, False)

    assert request is not None
    assert request.honor_type == "normal"
    assert request.group_type == "character"
    assert request.honor_level == 4
    assert request.honor_img_path == expected_paths["honor"].as_posix()
    assert request.rank_img_path == expected_paths["rank"].as_posix()
    assert request.frame_img_path == expected_paths["frame"].as_posix()
    assert request.scroll_img_path == expected_paths["scroll"].as_posix()
    assert request.lv_img_path == expected_paths["lv"].as_posix()
    assert request.lv6_img_path == expected_paths["lv6"].as_posix()


def test_masterdata_bonds_honor_request_builder_does_not_decode_images(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path)
    renderer.masterdata = tmp_path
    renderer.bonds_honors = {
        456: {
            "id": 456,
            "honorRarity": "middle",
            "gameCharacterUnitId1": 11,
            "gameCharacterUnitId2": 22,
        }
    }
    renderer.game_character_units = {
        11: {"gameCharacterId": 1},
        22: {"gameCharacterId": 2},
    }
    monkeypatch.setattr(renderer, "user_bonds_honor_level_for", lambda *_: 3)
    monkeypatch.setattr(renderer, "static_image_path", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(renderer, "first_region_asset", lambda rels: tmp_path / rels[0])
    monkeypatch.setattr(
        renderer,
        "open_rgba",
        lambda *_: pytest.fail("request derivation must not decode an image"),
    )

    request = renderer.build_masterdata_bonds_honor_request(
        {"id": 456, "wordId": 0, "inverse": True},
        False,
    )

    assert request is not None
    assert request.honor_type == "bonds"
    assert request.honor_level == 3
    assert request.honor_rarity == "middle"
    assert (request.chara_id, request.chara_id2) == ("22", "11")
    assert request.bonds_bg_path == (tmp_path / "honor" / "bonds" / "2_sub.png").as_posix()
    assert request.bonds_bg_path2 == (tmp_path / "honor" / "bonds" / "1_sub.png").as_posix()
    assert request.chara_icon_path == (tmp_path / "bonds_honor" / "character" / "chr_sd_22_01.png").as_posix()
    assert request.chara_icon_path2 == (tmp_path / "bonds_honor" / "character" / "chr_sd_11_01.png").as_posix()
    assert request.word_img_path is None


def test_custom_profile_request_asset_path_stays_inside_data_roots(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)
    safe = renderer.assets / "safe.png"
    _write_png(safe)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.png"
    _write_png(outside)

    assert renderer.resolve_request_asset_path(safe.as_posix()) == safe.resolve()
    with pytest.raises(ValueError, match="outside configured data roots"):
        renderer.resolve_request_asset_path(outside.as_posix())
    with pytest.raises(ValueError, match="traversal"):
        renderer.resolve_request_asset_path("../outside.png")


def test_custom_profile_source_image_budget_runs_before_decode(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path, max_layer_pixels=4)
    source = renderer.assets / "too-large.png"
    _write_png(source, (3, 2))

    with pytest.raises(ValueError, match="source asset"):
        renderer.open_rgba(source)


def test_custom_profile_vector_sdf_budget_runs_before_numpy_allocation(tmp_path: Path, monkeypatch) -> None:
    np = pytest.importorskip("numpy")
    renderer = _make_renderer(tmp_path, max_layer_pixels=4)
    monkeypatch.setattr(renderer, "tmp_vector_glyph_contours", lambda *_: ((), np))
    monkeypatch.setattr(np, "meshgrid", lambda *_args, **_kwargs: pytest.fail("meshgrid must not allocate"))

    with pytest.raises(ValueError, match="6 pixels"):
        renderer.tmp_vector_glyph_sdf_field(
            tmp_path / "font.ttf",
            "x",
            24.0,
            (0, 0, 3, 2),
            0,
            SimpleNamespace(gradient_scale=1.0),
        )


def test_custom_profile_vector_contour_builder_flattens_all_pen_segments() -> None:
    builder = _TMPGlyphContourBuilder(scale=2.0)

    builder.consume("moveTo", ((0.0, 0.0),))
    builder.consume("lineTo", ((1.0, 0.0),))
    builder.consume("qCurveTo", ((2.0, 1.0), (3.0, 0.0)))
    builder.consume("curveTo", ((4.0, 1.0), (5.0, 1.0), (6.0, 0.0)))
    builder.consume("closePath", ())

    contours = builder.finish()
    assert len(contours) == 1
    assert contours[0][0] == (0.0, 0.0)
    assert contours[0][1] == (2.0, 0.0)
    assert contours[0][-1] == (12.0, 0.0)


def test_custom_profile_vector_contour_builder_handles_implicit_quadratic_endpoints() -> None:
    builder = _TMPGlyphContourBuilder(scale=1.0)

    builder.consume("moveTo", ((0.0, 0.0),))
    builder.consume("qCurveTo", ((2.0, 2.0), (4.0, 0.0), None))
    builder.consume("endPath", ())

    contours = builder.finish()
    assert len(contours) == 1
    assert contours[0][0] == (0.0, 0.0)
    assert contours[0][-1] == (3.0, 1.0)


def test_custom_profile_retained_raster_budget_rejects_before_next_allocation(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path, max_scene_bytes=5)

    assert renderer._reserve_retained_raster_bytes(2, 3, label="custom profile test") == 5
    with pytest.raises(ValueError, match="would retain 6 bytes"):
        renderer._reserve_retained_raster_bytes(3, 3, label="custom profile test")


def test_custom_profile_warp_budget_runs_before_transform(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(
        tmp_path,
        canvas_w=64,
        canvas_h=64,
        origin_x=32,
        origin_y=32,
        position_scale_x=1.0,
        position_scale_y=1.0,
    )
    field = Image.new("L", (4, 4), 255)
    monkeypatch.setattr(Image.Image, "transform", lambda *_args, **_kwargs: pytest.fail("transform must not allocate"))

    with pytest.raises(ValueError, match="remaining limit"):
        renderer.warp_tmp_sdf_field_direct(
            field,
            0.0,
            0.0,
            (0.0, 0.0),
            {
                "position": {"x": 0, "y": 0},
                "scale": {"x": 1, "y": 1},
                "rotation": {"z": 0, "w": 1},
            },
            max_output_bytes=63,
        )


def test_custom_profile_direct_tmp_field_is_bounded_without_changing_geometry(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        canvas_w=2048,
        canvas_h=1260,
        max_layer_pixels=8_388_608,
    )

    assert renderer.tmp_direct_sdf_field_size((115_200, 1_920)) == (2_405, 1_920)


def test_custom_profile_bounded_tmp_field_warp_preserves_logical_quad(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        canvas_w=200,
        canvas_h=100,
        origin_x=0,
        origin_y=0,
        position_scale_x=1.0,
        position_scale_y=1.0,
    )
    object_data = {
        "position": {"x": 0, "y": 0},
        "scale": {"x": 1, "y": 1},
        "rotation": {"z": 0, "w": 1},
    }
    full = renderer.tmp_sdf_field_warp_plan(
        (800, 20),
        -100.0,
        10.0,
        (0.0, 0.0),
        object_data,
    )
    bounded = renderer.tmp_sdf_field_warp_plan(
        (20, 20),
        -100.0,
        10.0,
        (0.0, 0.0),
        object_data,
        geometry_size=(800, 20),
    )

    assert full is not None
    assert bounded is not None
    assert (bounded.size, bounded.left, bounded.top) == (full.size, full.left, full.top)
    assert bounded.affine[0] == pytest.approx(full.affine[0] * 20 / 800)
    assert bounded.affine[2] == pytest.approx(full.affine[2] * 20 / 800)
    assert bounded.affine[4:] == pytest.approx(full.affine[4:])


def test_custom_profile_tmp_field_warp_uses_rotated_logical_corners(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        canvas_w=100,
        canvas_h=100,
        origin_x=50,
        origin_y=50,
        position_scale_x=1.0,
        position_scale_y=1.0,
    )
    object_data = {
        "position": {"x": 0, "y": 0},
        "scale": {"x": 1, "y": 1},
        "rotation": {"z": 0, "w": 1},
    }
    corners = ((-10.0, -5.0), (5.0, -10.0), (10.0, 5.0), (-5.0, 10.0))

    plan = renderer.tmp_sdf_field_warp_plan(
        (10, 20),
        -10.0,
        -10.0,
        (0.0, 0.0),
        object_data,
        geometry_size=(20, 20),
        geometry_corners=corners,
    )

    assert plan is not None
    assert (plan.left, plan.top, plan.size) == (38, 38, (24, 24))
    inv00, inv01, c, inv10, inv11, f = plan.affine
    for point, expected in zip(corners[:3], ((0.0, 0.0), (10.0, 0.0), (10.0, 20.0)), strict=True):
        out_x = point[0] + 50.0 - plan.left
        out_y = point[1] + 50.0 - plan.top
        assert inv00 * out_x + inv01 * out_y + c == pytest.approx(expected[0])
        assert inv10 * out_x + inv11 * out_y + f == pytest.approx(expected[1])


def test_custom_profile_direct_tmp_glyph_passes_bounded_field_and_logical_geometry(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path, canvas_w=2048, canvas_h=1260)
    style = _base_tmp_style()
    char_info = SimpleNamespace(
        line_index=0,
        visible=True,
        style=style,
        char="A",
        bottom_left_x=0.0,
        top_left_x=0.0,
        top_right_x=115_200.0,
        bottom_right_x=115_200.0,
        bottom_left_y=0.0,
        top_left_y=1_920.0,
        top_right_y=1_920.0,
        bottom_right_y=0.0,
    )
    layout = SimpleNamespace(
        characters=[char_info],
        lines=[SimpleNamespace(index=0, width=115_200.0)],
    )
    atlas_path = tmp_path / "atlas.png"
    expected_field_size = (2_405, 1_920)
    monkeypatch.setattr(renderer, "tmp_native_unrotated_quad_size", lambda *_: (115_200, 1_920))

    def fake_render(*_args, **kwargs):
        assert kwargs["native_field_size"] == expected_field_size
        return (
            TMPStaticAtlasField(atlas_path, (1, 1), (0, 0, 1, 1), expected_field_size),
            None,
            (0, 0, *expected_field_size),
            0,
            0,
        )

    monkeypatch.setattr(renderer, "render_tmp_sdf_character_field", fake_render)

    prepared = renderer.prepare_tmp_direct_sdf_glyphs(
        "font",
        tmp_path / "font.ttf",
        layout,
        [0.0],
        "left",
        115_200.0,
        0.0,
        0.0,
        "#000000",
        0.0,
        defer_static_atlas=True,
    )

    assert prepared is not None
    assert len(prepared) == 1
    assert prepared[0][0].field_size == expected_field_size
    assert prepared[0][5] == (115_200, 1_920)
    assert prepared[0][6] is None


def test_custom_profile_direct_tmp_glyph_keeps_rich_text_rotation_corners(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path, canvas_w=200, canvas_h=100)
    style = replace(_base_tmp_style(), rotate=90.0)
    char_info = SimpleNamespace(
        line_index=0,
        visible=True,
        style=style,
        char="A",
        bottom_left_x=10.0,
        top_left_x=0.0,
        top_right_x=0.0,
        bottom_right_x=10.0,
        bottom_left_y=0.0,
        top_left_y=0.0,
        top_right_y=10.0,
        bottom_right_y=10.0,
    )
    layout = SimpleNamespace(
        characters=[char_info],
        lines=[SimpleNamespace(index=0, width=10.0)],
    )
    atlas_path = tmp_path / "atlas.png"
    monkeypatch.setattr(renderer, "tmp_native_unrotated_quad_size", lambda *_: (10, 10))
    monkeypatch.setattr(
        renderer,
        "render_tmp_sdf_character_field",
        lambda *_args, **_kwargs: (
            TMPStaticAtlasField(atlas_path, (1, 1), (0, 0, 1, 1), (10, 10)),
            None,
            (0, 0, 10, 10),
            0,
            0,
        ),
    )

    prepared = renderer.prepare_tmp_direct_sdf_glyphs(
        "font",
        tmp_path / "font.ttf",
        layout,
        [20.0],
        "left",
        10.0,
        0.0,
        0.0,
        "#000000",
        0.0,
        defer_static_atlas=True,
    )

    assert prepared is not None
    assert prepared[0][6] == ((0.0, 20.0), (0.0, 10.0), (10.0, 10.0), (10.0, 20.0))


def test_custom_profile_oversized_tmp_layer_falls_back_to_sparse_direct_render(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path, canvas_w=64, canvas_h=32)
    content = NativeContent(
        layer=1,
        kind="text",
        item={"text": "<line-indent=98%>A"},
        object_data={"visible": True},
    )
    error = RasterSizeLimitError(
        label="custom profile TMP text layer",
        width=44_033,
        height=309,
        max_pixels=8_388_608,
    )
    sparse_calls: list[RasterSizeLimitError] = []
    monkeypatch.setattr(renderer, "build_native_contents", lambda _card: [content])
    monkeypatch.setattr(renderer, "render_content_direct_on_card", lambda *_args: False)
    monkeypatch.setattr(renderer, "render_and_prepare_content_for_card", lambda _content: (_ for _ in ()).throw(error))
    monkeypatch.setattr(
        renderer,
        "render_oversized_tmp_text_direct",
        lambda _canvas, _content, exc: sparse_calls.append(exc) is None,
    )

    image = renderer.render_card({"customProfileCard": {}})

    assert image.size == (64, 32)
    assert sparse_calls == [error]


def test_custom_profile_static_tmp_field_can_defer_all_atlas_pixels(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path)
    style = _base_tmp_style()
    atlas_path = tmp_path / "atlas.png"
    metrics = SimpleNamespace(rect_x=10, rect_y=20, rect_w=6, rect_h=8, atlas_index=0)
    asset = SimpleNamespace(atlas_paths=[atlas_path], atlas_width=128.0, atlas_height=64.0, glyphs={ord("A"): metrics})
    char_info = SimpleNamespace(style=style)
    monkeypatch.setattr(renderer, "tmp_render_glyph_char", lambda *_: "A")
    monkeypatch.setattr(renderer, "tmp_static_sdf_asset", lambda *_: asset)
    monkeypatch.setattr(renderer, "tmp_native_unrotated_quad_size", lambda *_: (12, 16))
    monkeypatch.setattr(renderer, "tmp_native_atlas_padding", lambda *_: 2)
    monkeypatch.setattr(renderer, "tmp_native_vertex_scale_x", lambda *_: 1.0)
    monkeypatch.setattr(renderer, "tmp_atlas_alpha", lambda *_: pytest.fail("Pillow must not decode the atlas"))

    prepared = renderer.render_tmp_sdf_character_field(
        "font",
        tmp_path / "font.ttf",
        "A",
        style,
        24.0,
        "#000000",
        0.0,
        char_info,
        defer_static_atlas=True,
    )

    assert prepared is not None
    field, prepared_asset, bbox, pad_x, pad_y = prepared
    assert field == TMPStaticAtlasField(
        atlas_path=atlas_path,
        atlas_size=(128, 64),
        crop=(8, 34, 18, 46),
        field_size=(12, 16),
    )
    assert prepared_asset is asset
    assert bbox == (0, 0, 12, 16)
    assert (pad_x, pad_y) == (0, 0)


def test_custom_profile_dynamic_tmp_field_can_defer_all_glyph_pixels(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path)
    style = _base_tmp_style()
    font_path = tmp_path / "fonts" / "dynamic.ttf"
    font_path.touch()
    asset = SimpleNamespace(point_size=64.0, atlas_padding=3.0, gradient_scale=5.0)
    metrics = SimpleNamespace(bearing_x=-1.2, bearing_y=10.5, width=8.4, height=12.6)
    char_info = SimpleNamespace(style=style)
    monkeypatch.setattr(renderer, "tmp_render_glyph_char", lambda *_: "A")
    monkeypatch.setattr(renderer, "tmp_static_sdf_asset", lambda *_: None)
    monkeypatch.setattr(renderer, "tmp_sdf_asset", lambda *_: asset)
    monkeypatch.setattr(renderer.tmp_font_library, "metric_asset_candidates", lambda *_args, **_kwargs: [asset])
    monkeypatch.setattr(renderer.tmp_font_library, "runtime_source_font_path", lambda *_: font_path)
    monkeypatch.setattr(renderer.tmp_font_library, "_source_glyph_metrics_for_asset", lambda *_: metrics)
    monkeypatch.setattr(renderer, "tmp_native_unrotated_quad_size", lambda *_: (12, 16))
    monkeypatch.setattr(renderer, "tmp_native_atlas_padding", lambda *_: 2)
    monkeypatch.setattr(renderer, "tmp_dynamic_glyph_sdf", lambda *_args, **_kwargs: pytest.fail("no glyph pixels"))
    monkeypatch.setattr(renderer, "tmp_vector_glyph_contours", lambda *_: pytest.fail("no fontTools outlines"))

    prepared = renderer.render_tmp_sdf_character_field(
        "font",
        font_path,
        "A",
        style,
        24.0,
        "#000000",
        0.0,
        char_info,
        defer_dynamic_font=True,
    )

    assert prepared is not None
    field, prepared_asset, bbox, pad_x, pad_y = prepared
    assert field == TMPDynamicFontField(
        font_path=font_path,
        codepoint=ord("A"),
        sample_size=64.0,
        bbox=(-2, -11, 8, 3),
        padding=4,
        crop_padding=2,
        field_size=(12, 16),
        spread=4.9,
    )
    assert prepared_asset is asset
    assert bbox == (0, 0, 12, 16)
    assert (pad_x, pad_y) == (0, 0)


def test_custom_profile_static_tmp_field_builds_the_pillow_raster(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path)
    style = _base_tmp_style()
    atlas_path = tmp_path / "atlas.png"
    metrics = SimpleNamespace(
        rect_x=1,
        rect_y=2,
        rect_w=4,
        rect_h=6,
        atlas_index=0,
        bearing_x=1.0,
        bearing_y=5.0,
        glyph_scale=1.0,
    )
    asset = SimpleNamespace(atlas_paths=[atlas_path], point_size=24.0, glyphs={ord("A"): metrics})
    monkeypatch.setattr(renderer, "tmp_render_glyph_char", lambda *_: "A")
    monkeypatch.setattr(renderer, "tmp_static_sdf_asset", lambda *_: asset)
    monkeypatch.setattr(renderer, "tmp_atlas_alpha", lambda *_: Image.new("L", (32, 32), 255))
    monkeypatch.setattr(renderer, "tmp_display_padding", lambda *_: 2)
    monkeypatch.setattr(renderer, "tmp_native_vertex_scale_x", lambda *_: 1.0)

    prepared = renderer.render_tmp_sdf_character_field(
        "font",
        tmp_path / "font.ttf",
        "A",
        style,
        24.0,
        "#000000",
        0.0,
    )

    assert prepared is not None
    field, prepared_asset, bbox, pad_x, pad_y = prepared
    assert isinstance(field, Image.Image)
    assert field.size == (8, 10)
    assert prepared_asset is asset
    assert bbox == (1, -5, 5, 1)
    assert (pad_x, pad_y) == (2, 2)


def test_custom_profile_dynamic_tmp_field_scales_the_pillow_raster(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path)
    style = replace(_base_tmp_style(), scale_x=2.0)
    asset = SimpleNamespace(point_size=24.0)
    cached = TMPDynamicGlyphSDF(Image.new("L", (10, 8), 255), (0, 0, 6, 4), 2, 24.0)
    monkeypatch.setattr(renderer, "tmp_render_glyph_char", lambda *_: "A")
    monkeypatch.setattr(renderer, "tmp_static_sdf_asset", lambda *_: None)
    monkeypatch.setattr(renderer, "tmp_dynamic_glyph_sdf", lambda *_: (cached, asset))
    monkeypatch.setattr(renderer, "tmp_sdf_asset", lambda *_: asset)
    monkeypatch.setattr(renderer, "tmp_native_element_scale", lambda *_: 1.0)
    monkeypatch.setattr(renderer, "tmp_native_vertex_scale_x", lambda *_: 2.0)
    monkeypatch.setattr(renderer, "tmp_scale_x_bounds", lambda left, right, scale: (left * scale, right * scale))

    prepared = renderer.render_tmp_sdf_character_field(
        "font",
        tmp_path / "font.ttf",
        "A",
        style,
        24.0,
        "#000000",
        0.0,
    )

    assert prepared is not None
    field, prepared_asset, bbox, pad_x, pad_y = prepared
    assert isinstance(field, Image.Image)
    assert field.size == (20, 8)
    assert prepared_asset is asset
    assert bbox == (-4, -2, 16, 6)
    assert (pad_x, pad_y) == (4, 2)


def test_custom_profile_decorative_face_only_only_matches_symbol_rich_text(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path, tmp_decorative_face_only=True)
    decorative = {
        "text": "<color=#F9D2C0><size=160><scale=2.2>●",
        "outlineSize": 0.08361797034740448,
        "fontId": 1,
        "colorId": 1,
        "outlineColorId": 1,
    }
    normal = {
        "text": "<color=#F9D2C0>Hello",
        "outlineSize": 0.08361797034740448,
        "fontId": 1,
        "colorId": 1,
        "outlineColorId": 1,
    }

    assert renderer.is_decorative_text_item(decorative)
    assert renderer.decorative_outline_dilate(decorative, decorative["outlineSize"]) == 0.0
    assert not renderer.is_decorative_text_item(normal)
    assert renderer.decorative_outline_dilate(normal, normal["outlineSize"]) == normal["outlineSize"]


def test_custom_profile_decorative_face_only_matches_seq08_symbols(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path, tmp_decorative_face_only=True)
    decorative = {
        "text": "<scale=.8>▼〇∽︿>",
        "outlineSize": 0.1,
        "fontId": 1,
        "colorId": 1,
        "outlineColorId": 1,
    }

    assert renderer.is_decorative_text_item(decorative)
    assert renderer.decorative_outline_dilate(decorative, decorative["outlineSize"]) == 0.0


def test_custom_profile_decorative_direct_raster_is_default(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)
    decorative = {
        "text": "<color=#F9D2C0><size=160><scale=2.2>●",
        "outlineSize": 0.08361797034740448,
        "fontId": 1,
        "colorId": 1,
        "outlineColorId": 1,
    }

    assert renderer.is_decorative_text_item(decorative)
    assert renderer.decorative_outline_dilate(decorative, decorative["outlineSize"]) == 0.0
    assert renderer.tmp_decorative_face_only
    assert renderer.tmp_decorative_direct_raster
    assert not renderer.premultiply_alpha_transforms


def test_custom_profile_decorative_face_only_can_be_disabled(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path, tmp_decorative_face_only=False, tmp_decorative_direct_raster=False)
    decorative = {
        "text": "<color=#F9D2C0><size=160><scale=2.2>●",
        "outlineSize": 0.08361797034740448,
        "fontId": 1,
        "colorId": 1,
        "outlineColorId": 1,
    }

    assert renderer.is_decorative_text_item(decorative)
    assert renderer.decorative_outline_dilate(decorative, decorative["outlineSize"]) == decorative["outlineSize"]
    assert not renderer.tmp_decorative_face_only
    assert not renderer.tmp_decorative_direct_raster


def test_custom_profile_cli_uses_decorative_tmp_main_logic_by_default() -> None:
    parser = build_arg_parser()
    args = parser.parse_args([])

    assert args.tmp_decorative_face_only
    assert args.tmp_decorative_direct_raster
    assert not args.premultiply_alpha_transforms

    disabled = parser.parse_args(["--no-tmp-decorative-face-only", "--no-tmp-decorative-direct-raster"])
    assert not disabled.tmp_decorative_face_only
    assert not disabled.tmp_decorative_direct_raster


def test_custom_profile_cli_reports_only_enabled_deprecated_probes(capsys: pytest.CaptureFixture[str]) -> None:
    args = build_arg_parser().parse_args([])
    assert renderer_mod.deprecated_probe_args(args) == []

    args.position_scale = 1.25
    args.premultiply_alpha_transforms = True
    args.shape_sdf_source = "alpha"
    args.tmp_native_line_gap = not renderer_mod.DEFAULT_TMP_NATIVE_LINE_GAP
    args.skip_empty_lines = True
    expected = [
        "--position-scale",
        "--premultiply-alpha-transforms",
        "--shape-sdf-source=alpha",
        "--no-tmp-native-line-gap" if not args.tmp_native_line_gap else "--tmp-native-line-gap",
        "--skip-empty-lines",
    ]

    assert renderer_mod.deprecated_probe_args(args) == expected
    renderer_mod.warn_deprecated_probe_args(args)
    assert ", ".join(expected) in capsys.readouterr().err


def test_custom_profile_cli_validation_rejects_conflicts_and_warns_for_full_parallel_stage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = build_arg_parser()
    args = parser.parse_args([])
    args.full_canvas = True
    args.viewer_viewport = True
    with pytest.raises(SystemExit):
        renderer_mod.validate_cli_args(parser, args)

    args = parser.parse_args([])
    args.request = Path("request.json")
    args.export_request = Path("export.json")
    with pytest.raises(SystemExit):
        renderer_mod.validate_cli_args(parser, args)

    args = parser.parse_args([])
    args.request = Path("request.json")
    args.seq = 1
    with pytest.raises(SystemExit):
        renderer_mod.validate_cli_args(parser, args)

    args = parser.parse_args([])
    args.parallel_stage = "full"
    renderer_mod.validate_cli_args(parser, args)
    assert "--parallel-stage full is experimental" in capsys.readouterr().err


def test_custom_profile_cli_loads_request_profile_and_export_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parser = build_arg_parser()
    args = parser.parse_args([])
    args.request = tmp_path / "request.json"
    request_document = {"request": True}
    monkeypatch.setattr(renderer_mod, "load_json", lambda path: request_document if path == args.request else {})
    monkeypatch.setattr(
        renderer_mod,
        "decode_custom_profile_render_request",
        lambda document: ({"card": 1}, {"context": 2}, {"resources": 3}) if document is request_document else None,
    )
    assert renderer_mod.load_cli_render_job(parser, args) == (
        {"context": 2},
        [{"card": 1}],
        {"resources": 3},
    )

    args = parser.parse_args([])
    profile = {"profile": True}
    cards = [{"card": 1}]
    monkeypatch.setattr(renderer_mod, "load_json", lambda _path: profile)
    monkeypatch.setattr(renderer_mod, "normalize_profile_payload", lambda value: value)
    monkeypatch.setattr(renderer_mod, "select_custom_profile_cards", lambda *_args, **_kwargs: cards)
    monkeypatch.setattr(renderer_mod, "build_profile_context", lambda value: {"context": value})
    assert renderer_mod.load_cli_render_job(parser, args) == ({"context": profile}, cards, {})

    writes: list[tuple[Path, dict]] = []
    args.export_request = tmp_path / "export.json"
    monkeypatch.setattr(
        renderer_mod, "build_custom_profile_render_request", lambda value, card: {"p": value, "c": card}
    )
    monkeypatch.setattr(renderer_mod, "write_json", lambda path, value: writes.append((path, value)))
    assert renderer_mod.load_cli_render_job(parser, args) is None
    assert writes == [(args.export_request, {"p": profile, "c": cards[0]})]

    monkeypatch.setattr(renderer_mod, "select_custom_profile_cards", lambda *_args, **_kwargs: cards * 2)
    with pytest.raises(SystemExit):
        renderer_mod.load_cli_render_job(parser, args)


def test_custom_profile_cli_renders_cards_and_writes_jsonl_audits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    cards = [{"id": 1}, {"id": 2}]
    rendered: list[dict] = []
    fake_renderer = SimpleNamespace(
        render_card=lambda card: rendered.append(card) or Image.new("RGBA", (1, 1), (card["id"], 0, 0, 255))
    )
    monkeypatch.setattr(renderer_mod, "custom_profile_output_name", lambda card: f"card-{card['id']}.png")

    renderer_mod.render_cli_cards(fake_renderer, cards, out_dir)
    assert rendered == cards
    assert [Image.open(out_dir / f"card-{index}.png").getpixel((0, 0))[0] for index in (1, 2)] == [1, 2]

    audit_path = tmp_path / "audit" / "rows.jsonl"
    renderer_mod.write_cli_audit(audit_path, [{"kind": "text"}, {"kind": "shape"}])
    assert audit_path.read_text(encoding="utf-8").splitlines() == [
        '{"kind":"text"}',
        '{"kind":"shape"}',
    ]
    assert str(audit_path) in capsys.readouterr().out

    monkeypatch.setattr(renderer_mod, "custom_profile_output_name", lambda _card: "../escape.png")
    with pytest.raises(ValueError, match="unsafe custom profile output filename"):
        renderer_mod.render_cli_cards(fake_renderer, cards[:1], out_dir)
    assert not (tmp_path / "escape.png").exists()


def test_custom_profile_cli_main_dispatches_render_and_audit_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = build_arg_parser().parse_args([])
    args.out = tmp_path / "out"
    args.dump_tmp_layout = tmp_path / "tmp.jsonl"
    args.dump_native_audit = tmp_path / "native.jsonl"
    parser = SimpleNamespace(parse_args=lambda: args, error=lambda message: pytest.fail(message))
    cards = [{"id": 1}]
    fake_renderer = SimpleNamespace(tmp_layout_audit=[{"tmp": 1}], native_audit=[{"native": 1}])
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(renderer_mod, "build_arg_parser", lambda: parser)
    monkeypatch.setattr(renderer_mod, "resolve_cli_path", lambda path: path)
    monkeypatch.setattr(renderer_mod, "load_cli_render_job", lambda *_args: ({"profile": 1}, cards, {"asset": 2}))
    monkeypatch.setattr(renderer_mod, "resolve_render_target", lambda _args: "target")
    monkeypatch.setattr(
        renderer_mod,
        "build_renderer",
        lambda _args, profile, target, resources: (
            calls.append(("build", (profile, target, resources))) or fake_renderer
        ),
    )
    monkeypatch.setattr(
        renderer_mod,
        "render_cli_cards",
        lambda renderer, selected, out: calls.append(("render", (renderer, selected, out))),
    )
    monkeypatch.setattr(
        renderer_mod,
        "write_cli_audit",
        lambda path, rows: calls.append(("audit", (path, rows))),
    )

    renderer_mod.main()

    assert args.out.is_dir()
    assert calls == [
        ("build", ({"profile": 1}, "target", {"asset": 2})),
        ("render", (fake_renderer, cards, args.out)),
        ("audit", (args.dump_tmp_layout, fake_renderer.tmp_layout_audit)),
        ("audit", (args.dump_native_audit, fake_renderer.native_audit)),
    ]


def test_custom_profile_premul_resize_does_not_bleed_transparent_rgb() -> None:
    image = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    image.putpixel((0, 0), (255, 255, 255, 0))
    image.putpixel((1, 0), (255, 0, 0, 255))

    resized = resize_rgba_premul(image, (1, 1), Image.Resampling.BILINEAR)

    r, g, b, a = resized.getpixel((0, 0))
    assert a > 0
    assert r > 0
    assert g == 0
    assert b == 0


def test_custom_profile_harden_alpha_preserves_layer_opacity() -> None:
    image = Image.new("RGBA", (3, 1), (100, 120, 140, 0))
    image.putpixel((0, 0), (100, 120, 140, 0))
    image.putpixel((1, 0), (100, 120, 140, 32))
    image.putpixel((2, 0), (100, 120, 140, 64))

    hardened = harden_rgba_alpha(image, 8.0)

    assert hardened.getpixel((0, 0))[3] == 0
    assert hardened.getpixel((1, 0))[3] > 32
    assert hardened.getpixel((2, 0))[3] == 64


def test_custom_profile_direct_raster_preserves_mixed_layer_order(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        tmp_decorative_direct_raster=True,
        canvas_w=1,
        canvas_h=1,
        origin_x=0.0,
        origin_y=0.0,
    )
    direct_first = NativeContent(1, "text", {"id": 1}, {"visible": True})
    deferred_middle = NativeContent(2, "shape", {"id": 2}, {"visible": True})
    direct_last = NativeContent(3, "text", {"id": 3}, {"visible": True})
    renderer.build_native_contents = lambda card: [direct_first, deferred_middle, direct_last]  # type: ignore[method-assign]

    def draw_direct(canvas: Image.Image, content: NativeContent) -> bool:
        if content.kind != "text":
            return False
        color = (255, 0, 0, 255) if content.layer == 1 else (0, 0, 255, 255)
        canvas.alpha_composite(Image.new("RGBA", (1, 1), color), (0, 0))
        return True

    def draw_deferred(content: NativeContent) -> RenderedLayer:
        return RenderedLayer(
            content,
            "rendered",
            (Image.new("RGBA", (1, 1), (0, 255, 0, 255)), (0.0, 0.0)),
            PreparedLayer(Image.new("RGBA", (1, 1), (0, 255, 0, 255)), (0, 0)),
        )

    renderer.render_content_direct_on_card = draw_direct  # type: ignore[method-assign]
    renderer.render_and_prepare_content_for_card = draw_deferred  # type: ignore[method-assign]

    rendered = renderer.render_card({"customProfileCard": {}})

    assert rendered.getpixel((0, 0)) == (0, 0, 255, 255)


def test_custom_profile_serial_card_pipeline_records_and_composites_layers(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        tmp_decorative_direct_raster=False,
        parallel_stage="serial",
        canvas_w=1,
        canvas_h=1,
    )
    content = NativeContent(1, "shape", {"id": 1}, {"visible": True})
    layer = Image.new("RGBA", (1, 1), (1, 2, 3, 255))
    audits: list[tuple[str, object]] = []
    renderer._current_card_ref = {"cardId": 99}
    renderer.build_native_contents = lambda _card: [content]  # type: ignore[method-assign]
    renderer.render_content_for_card = lambda value: RenderedLayer(  # type: ignore[method-assign]
        value, "rendered", (layer, (0.0, 0.0))
    )
    renderer.prepare_layers_for_card = lambda _layers: [PreparedLayer(layer, (0, 0)), None]  # type: ignore[method-assign]
    renderer.record_native_audit = (  # type: ignore[method-assign]
        lambda _card_ref, _content, status, result: audits.append((status, result))
    )

    rendered = renderer.render_card({"customProfileCard": {}})

    assert rendered.getpixel((0, 0)) == (1, 2, 3, 255)
    assert audits == [("rendered", (layer, (0.0, 0.0)))]
    assert renderer._current_card_ref == {"cardId": 99}


def test_custom_profile_full_parallel_card_pipeline_records_prepared_layers(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        tmp_decorative_direct_raster=False,
        parallel_stage="full",
        parallel_workers=2,
        canvas_w=1,
        canvas_h=1,
    )
    first = NativeContent(1, "shape", {"id": 1}, {"visible": True})
    second = NativeContent(2, "shape", {"id": 2}, {"visible": True})
    layer = Image.new("RGBA", (1, 1), (4, 5, 6, 255))
    audits: list[str] = []
    renderer.build_native_contents = lambda _card: [first, second]  # type: ignore[method-assign]
    renderer.render_contents_for_card_parallel = lambda _contents: [  # type: ignore[method-assign]
        RenderedLayer(first, "empty", None),
        RenderedLayer(second, "rendered", (layer, (0.0, 0.0)), PreparedLayer(layer, (0, 0))),
    ]
    renderer.record_native_audit = (  # type: ignore[method-assign]
        lambda _card_ref, _content, status, _result: audits.append(status)
    )

    rendered = renderer.render_card({"customProfileCard": {}})

    assert rendered.getpixel((0, 0)) == (4, 5, 6, 255)
    assert audits == ["empty", "rendered"]


def test_custom_profile_oversized_direct_fallback_reraises_when_sparse_path_rejects(
    tmp_path: Path,
) -> None:
    renderer = _make_renderer(tmp_path, canvas_w=1, canvas_h=1)
    content = NativeContent(1, "text", {"id": 1}, {"visible": True})
    error = RasterSizeLimitError(label="layer", width=2, height=2, max_pixels=1)
    renderer._current_card_ref = {"cardId": 77}
    renderer.build_native_contents = lambda _card: [content]  # type: ignore[method-assign]
    renderer.render_content_direct_on_card = lambda *_args: False  # type: ignore[method-assign]
    renderer.render_and_prepare_content_for_card = (  # type: ignore[method-assign]
        lambda _content: (_ for _ in ()).throw(error)
    )
    renderer.render_oversized_tmp_text_direct = lambda *_args: False  # type: ignore[method-assign]

    with pytest.raises(RasterSizeLimitError) as exc_info:
        renderer.render_card({"customProfileCard": {}})

    assert exc_info.value is error
    assert renderer._current_card_ref == {"cardId": 77}


def test_custom_profile_stamp_uses_cloud_region_asset_layout(tmp_path: Path) -> None:
    stamp_path = tmp_path / "asset" / "cn-assets" / "startapp" / "stamp" / "stamp0230" / "stamp0230.png"
    stamp_path.parent.mkdir(parents=True)
    stamp_path.write_bytes(b"png")

    renderer = _make_renderer(
        tmp_path,
        resources={
            "stamps": {146: {"id": 146, "assetbundleName": "stamp0230"}},
            "stampAssets": {
                146: {
                    "id": 146,
                    "assetbundleName": "stamp0230",
                    "imagePath": "asset/cn-assets/startapp/stamp/stamp0230/stamp0230.png",
                }
            },
        },
    )

    assert renderer.resolve_request_asset_path(renderer.stamp_assets[146]["imagePath"]) == stamp_path


def test_custom_profile_stamp_does_not_use_non_cloud_stamp_filename(tmp_path: Path) -> None:
    stamp_path = tmp_path / "asset" / "cn-assets" / "startapp" / "stamp" / "stamp0230" / "stamp.png"
    stamp_path.parent.mkdir(parents=True)
    stamp_path.write_bytes(b"png")

    renderer = _make_renderer(tmp_path, resources={"stamps": {146: {"id": 146, "assetbundleName": "stamp0230"}}})

    assert renderer.stamp_resource_path(renderer.stamps[146]) is None


def test_custom_profile_resource_path_requires_cloud_image_path_without_masterdata(tmp_path: Path) -> None:
    bg_path = tmp_path / "asset" / "cn-assets" / "startapp" / "custom_profile" / "bg" / "profile_bg_pattern_0001.png"
    _write_png(bg_path)

    renderer = _make_renderer(
        tmp_path,
        resources={
            "customProfileGeneralBackgroundResources": {
                1: {
                    "id": 1,
                    "resourceLoadVal": "custom_profile/bg",
                    "fileName": "profile_bg_pattern_0001",
                }
            }
        },
    )
    assert renderer.resource_path(renderer.general_bgs[1]) is None

    renderer = _make_renderer(
        tmp_path,
        resources={
            "customProfileGeneralBackgroundResources": {
                1: {
                    "id": 1,
                    "resourceLoadVal": "custom_profile/bg",
                    "fileName": "profile_bg_pattern_0001",
                    "imagePath": "asset/cn-assets/startapp/custom_profile/bg/profile_bg_pattern_0001.png",
                }
            }
        },
    )
    assert renderer.resource_path(renderer.general_bgs[1]) == bg_path


def test_custom_profile_v67_image_buckets_use_cloud_resources(tmp_path: Path) -> None:
    resource_specs = (
        (
            "characterIcons",
            "character_icon",
            "customProfileCharacterIconResources",
            21,
            "character_icon/profile_chr_icon_miku.png",
        ),
        (
            "materials",
            "material",
            "customProfileMaterialResources",
            1,
            "material/profile_icon_item_0001.png",
        ),
        (
            "userInterfaceIcons",
            "user_interface_icon",
            "customProfileUserInterfaceIconResources",
            42,
            "user_interface_icon/profile_icon_0042.png",
        ),
    )
    resources: dict[str, dict[int, dict[str, object]]] = {}
    layout: dict[str, list[dict[str, object]]] = {}
    for layer, (bucket, _kind, resource_key, resource_id, relative_path) in enumerate(resource_specs, start=1):
        image_path = tmp_path / "asset" / "jp-assets" / "startapp" / "custom_profile" / relative_path
        _write_png(image_path)
        resources[resource_key] = {
            resource_id: {
                "id": resource_id,
                "imagePath": image_path.relative_to(tmp_path).as_posix(),
            }
        }
        layout[bucket] = [
            {
                "id": resource_id,
                "objectData": {
                    "layer": layer,
                    "visible": True,
                    "position": {"x": 0, "y": 0},
                    "scale": {"x": 1, "y": 1},
                    "rotation": {"z": 0, "w": 1},
                },
            }
        ]

    renderer = _make_renderer(tmp_path, resources=resources, region="jp")
    contents = renderer.build_native_contents({"customProfileCard": layout})

    assert [content.kind for content in contents] == [spec[1] for spec in resource_specs]
    for content in contents:
        resource = renderer.image_resource_for(content.kind, content.item)
        assert renderer.resource_path(resource) is not None
        rendered = renderer.render_image_content(content.kind, content.item)
        assert isinstance(rendered, tuple)
        assert rendered[0].size == (3, 2)


def test_custom_profile_null_content_buckets_are_treated_as_empty(tmp_path: Path) -> None:
    nullable_buckets = (
        "generals",
        "generalBackgrounds",
        "storyBackgrounds",
        "standMembers",
        "cardMembers",
        "honors",
        "bondsHonors",
        "collections",
        "others",
        "characterIcons",
        "materials",
        "userInterfaceIcons",
        "stamps",
        "texts",
        "miniCharas",
        "screenFilters",
    )
    layout = dict.fromkeys(nullable_buckets)
    layout["shapes"] = [
        {
            "id": 1,
            "objectData": {
                "layer": 7,
                "visible": True,
                "position": {"x": 0, "y": 0},
                "scale": {"x": 1, "y": 1},
                "rotation": {"z": 0, "w": 1},
            },
        }
    ]

    renderer = _make_renderer(tmp_path)
    contents = renderer.build_native_contents({"customProfileCard": layout})

    assert [(content.kind, content.object_data["layer"]) for content in contents] == [("shape", 7)]
    assert renderer.build_native_contents({"customProfileCard": None}) == []


def test_custom_profile_card_member_candidates_match_cloud_small_still_paths(tmp_path: Path) -> None:
    _write_png(
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_small"
        / "res010_no034"
        / "card_after_training.png"
    )
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userCards": [
                {
                    "cardId": 915,
                    "specialTrainingStatus": "done",
                    "defaultImage": "special_training",
                }
            ],
        },
        resources={
            "cards": {915: {"id": 915, "assetbundleName": "res010_no034", "cardRarityType": "rarity_4"}},
            "cardAssets": {
                915: {
                    "id": 915,
                    "assetbundleName": "res010_no034",
                    "smallAfterTrainingPath": (
                        "asset/cn-assets/startapp/character/member_small/res010_no034/card_after_training.png"
                    ),
                }
            },
        },
    )

    candidates = [
        path.as_posix()
        for path in renderer.card_member_image_candidates({"id": 915, "type": 2, "useAfterSpecialTraining": True})
    ]

    assert any(path.endswith("/character/member_small/res010_no034/card_after_training.png") for path in candidates)
    assert not any(path.endswith("/character/member/res010_no034/card_after_training.png") for path in candidates)
    assert not any("/member_cutout/" in path for path in candidates)
    assert not any("/thumbnail/chara/" in path for path in candidates)


def test_custom_profile_card_member_clip_type_prefers_deck_cutout_path(tmp_path: Path) -> None:
    _write_png(
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_cutout"
        / "res010_no034"
        / "after_training.png"
    )
    _write_png(
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member"
        / "res010_no034"
        / "card_after_training.png"
    )
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userCards": [
                {
                    "cardId": 915,
                    "specialTrainingStatus": "done",
                    "defaultImage": "special_training",
                }
            ],
        },
        resources={
            "cards": {915: {"id": 915, "assetbundleName": "res010_no034", "cardRarityType": "rarity_4"}},
            "cardAssets": {
                915: {
                    "id": 915,
                    "assetbundleName": "res010_no034",
                    "afterTrainingPath": (
                        "asset/cn-assets/startapp/character/member/res010_no034/card_after_training.png"
                    ),
                    "deckAfterTrainingPath": (
                        "asset/cn-assets/startapp/character/member_cutout/res010_no034/after_training.png"
                    ),
                }
            },
        },
    )

    candidates = [
        path.as_posix()
        for path in renderer.card_member_image_candidates({"id": 915, "type": 1, "useAfterSpecialTraining": True})
    ]

    assert candidates[0].endswith("/character/member_cutout/res010_no034/after_training.png")
    assert not candidates[0].endswith("/character/member/res010_no034/card_after_training.png")


def test_custom_profile_card_member_uses_saved_training_state(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userCards": [
                {
                    "cardId": 915,
                    "specialTrainingStatus": "done",
                    "defaultImage": "normal",
                }
            ],
        },
        resources={
            "cards": {915: {"id": 915, "assetbundleName": "res010_no034", "cardRarityType": "rarity_4"}},
        },
    )

    assert renderer.card_member_after_training({"id": 915, "useAfterSpecialTraining": True})


def test_custom_profile_card_member_full_type_prefers_small_still_path(tmp_path: Path) -> None:
    _write_png(
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_small"
        / "res010_no034"
        / "card_after_training.png"
    )
    _write_png(
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member"
        / "res010_no034"
        / "card_after_training.png"
    )
    renderer = _make_renderer(
        tmp_path,
        resources={
            "cards": {915: {"id": 915, "assetbundleName": "res010_no034", "cardRarityType": "rarity_4"}},
            "cardAssets": {
                915: {
                    "id": 915,
                    "assetbundleName": "res010_no034",
                    "afterTrainingPath": (
                        "asset/cn-assets/startapp/character/member/res010_no034/card_after_training.png"
                    ),
                    "smallAfterTrainingPath": (
                        "asset/cn-assets/startapp/character/member_small/res010_no034/card_after_training.png"
                    ),
                }
            },
        },
    )

    candidates = [
        path.as_posix()
        for path in renderer.card_member_image_candidates({"id": 915, "type": 2, "useAfterSpecialTraining": True})
    ]

    assert candidates[0].endswith("/character/member_small/res010_no034/card_after_training.png")
    assert not any(path.endswith("/character/member/res010_no034/card_after_training.png") for path in candidates)


def test_custom_profile_card_display_list_builders_are_stable_pillow_free_and_unmasked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = _make_renderer(
        tmp_path,
        profile_context={"userCards": [{"cardId": 915, "level": 60, "masterRank": 5}]},
        resources={
            "cards": {
                915: {
                    "id": 915,
                    "assetBundleName": "res010_no034",
                    "cardRarityType": "rarity_4",
                    "attr": "cute",
                }
            }
        },
    )
    art_path = tmp_path / "not-decoded-during-build.png"

    def fail_decode(*_args, **_kwargs):
        raise AssertionError("building a CardDisplayList must not decode Pillow images")

    monkeypatch.setattr(renderer, "open_checked_image", fail_decode)
    monkeypatch.setattr(renderer, "card_image_path_for_state", lambda *_args, **_kwargs: art_path)
    monkeypatch.setattr(renderer, "card_member_image_path", lambda _item: art_path)
    full = renderer.build_small_still_card_display_list(915, art_path, target_size=(940, 530))
    deck = renderer.build_deck_card_display_list(
        915,
        art_path,
        native_size=(330, 512),
        art_size=(330.0, 512.0),
        crop_align_y=0.0,
        mask_sprite_name=None,
        render_size=(156, 242),
    )
    leader = renderer.build_profile_leader_card_display_list(915)
    profile_deck = renderer.build_profile_deck_card_display_list(915, leader=True)
    clip = renderer.build_card_member_display_list({"id": 915, "type": 1, "showMasterRank": True})
    full_member = renderer.build_card_member_display_list({"id": 915, "type": 2, "showMasterRank": True})
    placeholder = renderer.build_empty_profile_deck_card_display_list((156, 242))

    assert isinstance(full.ops[0], CardCoverArtOp)
    assert isinstance(deck.ops[0], CardCoverArtOp)
    assert deck.render_size == (156, 242)
    assert not any(isinstance(op, CardAlphaMaskOp) for op in full.ops + deck.ops)
    assert leader is not None
    assert leader.size == (940, 530)
    assert profile_deck is not None
    assert profile_deck.render_size == (156, 242)
    assert clip is not None
    assert clip.size == (328, 520)
    assert full_member is not None
    assert full_member.size == (940, 530)
    assert placeholder.size == (156, 242)


def test_custom_profile_leader_card_uses_small_still_path(tmp_path: Path) -> None:
    small_path = (
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_small"
        / "res010_no034"
        / "card_after_training.png"
    )
    full_path = (
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member"
        / "res010_no034"
        / "card_after_training.png"
    )
    _write_png_color(small_path, (940, 530), (0, 255, 0, 255))
    _write_png_color(full_path, (940, 530), (255, 0, 0, 255))
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userCards": [
                {
                    "cardId": 915,
                    "specialTrainingStatus": "done",
                    "defaultImage": "special_training",
                }
            ],
        },
        resources={
            "cards": {915: {"id": 915, "assetBundleName": "res010_no034", "cardRarityType": "rarity_4"}},
            "cardAssets": {
                915: {
                    "id": 915,
                    "assetBundleName": "res010_no034",
                    "afterTrainingPath": full_path.as_posix(),
                    "smallAfterTrainingPath": small_path.as_posix(),
                }
            },
        },
    )

    image = renderer.compose_profile_leader_card(915)

    assert image is not None
    assert image.size == (940, 530)
    assert image.getpixel((470, 265))[:3] == (0, 255, 0)


def test_custom_profile_card_member_full_type_renders_small_still_frame(tmp_path: Path) -> None:
    small_path = (
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_small"
        / "res010_no034"
        / "card_after_training.png"
    )
    _write_png_color(small_path, (940, 530), (255, 0, 0, 255))
    _write_png_color(tmp_path / "static_images" / "customprofile" / "cardFrame_L_4.png", (940, 530), (0, 255, 0, 255))
    renderer = _make_renderer(
        tmp_path,
        resources={
            "cards": {915: {"id": 915, "assetBundleName": "res010_no034", "cardRarityType": "rarity_4"}},
            "cardAssets": {
                915: {
                    "id": 915,
                    "assetBundleName": "res010_no034",
                    "smallAfterTrainingPath": small_path.as_posix(),
                }
            },
        },
    )

    rendered = renderer.render_card_member_content(
        {"id": 915, "type": 2, "useAfterSpecialTraining": True, "showMasterRank": True}
    )

    assert isinstance(rendered, tuple)
    assert rendered[0].size == (940, 530)
    assert rendered[0].getpixel((10, 10))[:3] == (0, 255, 0)


def test_custom_profile_card_member_clip_type_renders_deck_card_frame(tmp_path: Path) -> None:
    clip_path = (
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_cutout"
        / "res010_no034"
        / "after_training.png"
    )
    _write_png_color(clip_path, (328, 538), (255, 0, 0, 255))
    _write_png_color(tmp_path / "static_images" / "customprofile" / "cardFrame_M_4.png", (312, 512), (0, 255, 0, 255))
    _write_png_color(tmp_path / "static_images" / "customprofile" / "tex_mask_card_s.png", (174, 212), (0, 0, 0, 255))
    renderer = _make_renderer(
        tmp_path,
        profile_context={"userCards": [{"cardId": 915, "level": 60, "masterRank": 5}]},
        resources={
            "cards": {915: {"id": 915, "assetBundleName": "res010_no034", "cardRarityType": "rarity_4"}},
            "cardAssets": {
                915: {
                    "id": 915,
                    "assetBundleName": "res010_no034",
                    "deckAfterTrainingPath": clip_path.as_posix(),
                }
            },
        },
    )

    rendered = renderer.render_card_member_content(
        {"id": 915, "type": 1, "useAfterSpecialTraining": True, "showMasterRank": True}
    )

    assert isinstance(rendered, tuple)
    assert rendered[0].size == (328, 520)
    assert rendered[0].getpixel((10, 10))[:3] == (0, 255, 0)


def test_custom_profile_collection_prefers_image_asset_for_badges(tmp_path: Path) -> None:
    asset_path = (
        tmp_path / "asset" / "cn-assets" / "startapp" / "custom_profile" / "collection" / "collab001" / "badge.png"
    )
    _write_png(asset_path, (25, 25))
    renderer = _make_renderer(
        tmp_path,
        resources={
            "customProfileCollectionResources": {
                801: {
                    "id": 801,
                    "customProfileResourceCollectionType": "can_badge",
                    "imagePath": "asset/cn-assets/startapp/custom_profile/collection/collab001/badge.png",
                }
            }
        },
    )

    rendered = renderer.render_collection_content({"id": 801})

    assert isinstance(rendered, tuple)
    assert rendered[0].size == (25, 25)


def test_custom_profile_omikuji_collection_uses_target_master_row(tmp_path: Path) -> None:
    material_dir = tmp_path / "asset" / "jp-assets" / "startapp" / "lottery_game" / "new_year_2026_material"
    _write_png(material_dir / "bg_omikuji_MORE MORE JUMP.png", (1480, 490))
    _write_png(material_dir / "unsei_daikichi.png", (24, 80))
    renderer = _make_renderer(
        tmp_path,
        resources={
            "customProfileCollectionResources": {
                1000: {
                    "id": 1000,
                    "customProfileResourceCollectionType": "omikuji",
                    "resourceLoadVal": "lottery_game/new_year_2026",
                    "fileName": "Prefabs/Omikuji",
                }
            },
            "omikujis": {
                183: {
                    "id": 183,
                    "unit": "idol",
                    "fortuneType": "grate_fortune",
                    "summary": "過去の悔恨が晴れる\n年になるでしょう\n迷いは捨て挑むべし",
                    "title1": "願望",
                    "description1": "必ず叶う",
                    "title2": "健康",
                    "description2": "大変良好",
                    "title3": "待人",
                    "description3": "自ら行くがよし",
                    "unitAssetbundleName": "lottery_game/new_year_2026_material",
                    "fortuneAssetbundleName": "lottery_game/new_year_2026_material",
                    "omikujiCoverAssetbundleName": "lottery_game/new_year_2026_material",
                    "unitFilePath": "bird_MORE MORE JUMP",
                    "fortuneFilePath": "unsei_daikichi",
                    "omikujiCoverFilePath": "omikuji_MORE MORE JUMP",
                }
            },
        },
        region="jp",
    )

    rendered = renderer.render_collection_content({"id": 1000, "targetId": 183})

    assert isinstance(rendered, tuple)
    assert rendered[0].size == (1480, 490)
    assert rendered[0].getchannel("A").getbbox() is not None


def test_custom_profile_omikuji_collection_requires_material_assets(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        resources={
            "customProfileCollectionResources": {
                1000: {
                    "id": 1000,
                    "customProfileResourceCollectionType": "omikuji",
                    "resourceLoadVal": "lottery_game/new_year_2026",
                    "fileName": "Prefabs/Omikuji",
                }
            },
            "omikujis": {
                183: {
                    "id": 183,
                    "unit": "idol",
                    "fortuneType": "grate_fortune",
                    "summary": "過去の悔恨が晴れる",
                    "unitAssetbundleName": "lottery_game/new_year_2026_material",
                    "fortuneAssetbundleName": "lottery_game/new_year_2026_material",
                    "omikujiCoverAssetbundleName": "lottery_game/new_year_2026_material",
                    "unitFilePath": "bird_MORE MORE JUMP",
                    "fortuneFilePath": "unsei_daikichi",
                    "omikujiCoverFilePath": "omikuji_MORE MORE JUMP",
                }
            },
        },
        region="jp",
    )

    rendered = renderer.render_collection_content({"id": 1000, "targetId": 183})

    assert isinstance(rendered, NativeUnresolvedContent)
    assert rendered.reason == "omikuji collection needs material asset(s): background, fortune"


def test_custom_profile_omikuji_collection_requires_target_master_row(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        resources={
            "customProfileCollectionResources": {
                1000: {
                    "id": 1000,
                    "customProfileResourceCollectionType": "omikuji",
                }
            }
        },
        region="jp",
    )

    rendered = renderer.render_collection_content({"id": 1000, "targetId": 183})

    assert isinstance(rendered, NativeUnresolvedContent)
    assert rendered.reason == "omikuji collection needs the target omikujis.json row"


def test_custom_profile_music_clear_info_uses_profile_counts(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userMusicDifficultyClearCount": [
                {"musicDifficultyType": "easy", "liveClear": 1, "fullCombo": 2, "allPerfect": 3},
                {"musicDifficultyType": "master", "liveClear": 4, "fullCombo": 5, "allPerfect": 6},
            ],
        },
    )

    image = renderer.render_general_music_clear_info()

    assert image.size == (860, 318)
    assert renderer.music_clear_count_map()["master"]["fullCombo"] == 5
    assert renderer.music_clear_count_map()["master"]["allPerfect"] == 6
    assert _image_has_content_in_box(image, (344, 12, 516, 50))
    assert _image_has_content_in_box(image, (344, 174, 516, 212))
    assert not _image_has_content_in_box(image, (344, 224, 516, 238))


def test_custom_profile_music_clear_select_tab_info_draws_value_panel(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)

    image = renderer.render_general_music_clear_select_tab_info()

    assert image.size == (860, 166)
    assert _image_has_content_in_box(image, (32, 80, 828, 158))
    assert not _image_has_content_in_box(image, (32, 58, 828, 72))


def test_custom_profile_general_x_uses_twitter_id(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path, profile_context={"userProfile": {"twitterId": "sekai_test"}})

    image = renderer.render_general_x()

    assert image.size == (548, 64)
    assert _image_has_content_in_box(image, (20, 12, 74, 52))
    assert _image_has_content_in_box(image, (95, 12, 420, 52))


def test_custom_profile_jp_general_labels_are_localized(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path, region="jp")

    assert renderer.general_text("comment_title") == "ひと言"
    assert renderer.general_text("total_power") == "総合力"
    assert renderer.general_text("character_rank_tab") == "キャラクターランク"


def test_custom_profile_general_content_maps_jp_x(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        profile_context={"userProfile": {"twitterId": "sekai_test"}},
        resources={"customProfilePlayerInfoResources": {1: {"id": 1, "fileName": "X"}}},
        region="jp",
    )

    rendered = renderer.render_general_content({"type": 1})

    assert isinstance(rendered, tuple)
    assert rendered[0].size == (548, 64)


def test_custom_profile_chara_rank_icons_can_be_passed_by_cloud(tmp_path: Path) -> None:
    icon_path = tmp_path / "static_images" / "chara_icon" / "miku.png"
    _write_png(icon_path, (9, 4))
    (tmp_path / "static_images" / "card").mkdir(parents=True)
    renderer = _make_renderer(
        tmp_path,
        resources={"charaRankIconPathMap": {"21": "static_images/chara_icon/miku.png"}},
    )

    assert renderer.chara_rank_icon_path(21) == icon_path


def test_custom_profile_character_rank_component_keeps_challenge_stage_off_rank_tab(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userCharacters": [{"characterId": 21, "characterRank": 28}],
            "userChallengeLiveSoloStages": [
                {"characterId": 21, "rank": 1},
                {"characterId": 21, "rank": 7},
            ],
        },
    )

    image = renderer.render_general_character_rank_and_challenge_stage(scroll=True)
    first_pixels = image.tobytes()
    renderer.profile_context["userChallengeLiveSoloStages"] = [{"characterId": 21, "rank": 150}]
    second = renderer.render_general_character_rank_and_challenge_stage(scroll=True)

    assert image.size == (908, 550)
    assert renderer.character_rank_map()[21] == 28
    assert renderer.challenge_live_stage_map()[21] == 150
    assert renderer.challenge_live_rank_for(21) == 150
    assert second.tobytes() == first_pixels

    adapter = PillowGeneralPrefabAdapter(renderer.general_font, renderer.paste_unity_sprite, renderer.open_rgba)
    display_list = build_general_prefab_display_list(
        "CharacterRankAndChallengeStageScroll",
        size=GENERAL_NATIVE_SIZES["CharacterRankAndChallengeStageScroll"],
        profile_context=renderer.profile_context,
        labels={
            "character_rank_tab": renderer.general_text("character_rank_tab"),
            "challenge_stage_tab": renderer.general_text("challenge_stage_tab"),
        },
        metrics=adapter,
        palette=GENERAL_PREFAB_PALETTE,
        asset_paths={
            f"character_rank_icon:{character_id}": renderer.chara_icon_path(character_id)
            for _nickname, character_id in CHARA_LIST
            if character_id is not None
        },
    )
    assert display_list is not None
    viewport = display_list.ops[4]
    assert isinstance(viewport, GeneralViewportOp)
    texts = [op.text for op in viewport.children if isinstance(op, GeneralTextOp)]
    assert "28" in texts
    assert "150" not in texts
    assert adapter.render(display_list).tobytes() == second.tobytes()


def test_custom_profile_character_rank_scroll_masks_fifth_row_text(tmp_path: Path) -> None:
    icon_path = tmp_path / "asset" / "cn-assets" / "startapp" / "custom_profile" / "icons" / "9.png"
    _write_png_color(icon_path, (16, 16), (255, 0, 0, 255))
    renderer = _make_renderer(
        tmp_path,
        profile_context={"userCharacters": [{"characterId": 9, "characterRank": 49}]},
        resources={"charaRankIconPathMap": {"9": icon_path.as_posix()}},
    )

    image = renderer.render_general_character_rank_and_challenge_stage(scroll=True)

    assert image.size == (908, 550)
    # Row five starts at content y=399.5. Its icon begins at y=404, so the first
    # 16 pixels survive the 420-pixel viewport while its rank text at y=453 is masked.
    assert image.getpixel((50, 512)) == (255, 0, 0, 255)
    assert not _image_has_content_in_box(image, (24, 525, 884, 550))

    adapter = PillowGeneralPrefabAdapter(renderer.general_font, renderer.paste_unity_sprite, renderer.open_rgba)
    display_list = build_general_prefab_display_list(
        "CharacterRankAndChallengeStageScroll",
        size=GENERAL_NATIVE_SIZES["CharacterRankAndChallengeStageScroll"],
        profile_context=renderer.profile_context,
        labels={
            "character_rank_tab": renderer.general_text("character_rank_tab"),
            "challenge_stage_tab": renderer.general_text("challenge_stage_tab"),
        },
        metrics=adapter,
        palette=GENERAL_PREFAB_PALETTE,
        asset_paths={
            f"character_rank_icon:{character_id}": renderer.chara_icon_path(character_id)
            for _nickname, character_id in CHARA_LIST
            if character_id is not None
        },
    )
    assert display_list is not None
    viewport = display_list.ops[4]
    assert isinstance(viewport, GeneralViewportOp)
    row_five_rank = next(op for op in viewport.children if isinstance(op, GeneralTextOp) and op.text == "49")
    assert row_five_rank.pos == (140.5, 453.0)
    assert row_five_rank.pos[1] > viewport.viewport_size[1]
    assert adapter.render(display_list).tobytes() == image.tobytes()


def test_custom_profile_character_rank_full_size_is_bottom_aligned(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)

    image = renderer.render_general_character_rank_and_challenge_stage(scroll=False)

    assert image.size == (908, 813)
    assert _image_has_content_in_box(image, (100, 760, 830, 790))


def test_custom_profile_character_rank_value_text_matches_prefab_rect(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path)
    calls = []

    def capture_text_rect(self, draw, rect, text, *, size, fill):
        calls.append((rect, text, size))

    monkeypatch.setattr(PNGRenderer, "draw_center_text_rect", capture_text_rect)

    image = Image.new("RGBA", (196, 85), (0, 0, 0, 0))
    renderer.draw_profile_rank_and_stage_cell(image, (0.0, 0.0), 21, 28)

    assert calls == [((59.0, 29.0, 191.0, 78.0), "28", 31)]


def test_custom_profile_chara_rank_icons_require_cloud_path(tmp_path: Path) -> None:
    icon_path = tmp_path / "static_images" / "chara_icon" / "miku.png"
    _write_png(icon_path, (9, 4))
    (tmp_path / "static_images" / "card").mkdir(parents=True)
    renderer = _make_renderer(tmp_path)

    assert renderer.chara_rank_icon_path(21) is None


def test_custom_profile_story_favorite_uses_cloud_resources(tmp_path: Path) -> None:
    banner_path = tmp_path / "asset" / "cn-assets" / "startapp" / "event_story" / "event_test" / "screen_image"
    _write_png(banner_path / "banner_event_story.png", (128, 64))
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userStoryFavorites": [
                {"shareNo": 2, "storyType": "event_story", "storyId": 20},
                {"shareNo": 1, "storyType": "event_story", "storyId": 10},
            ]
        },
        resources={
            "storyFavoriteResources": {
                "event_story:10": {
                    "title": "First",
                    "imagePath": "asset/cn-assets/startapp/event_story/event_test/screen_image/banner_event_story.png",
                }
            }
        },
    )

    image = renderer.render_general_story_favorite()

    assert image is not None
    assert image.size == (909, 813)
    assert renderer.ordered_story_favorites(renderer.profile_context["userStoryFavorites"])[0]["storyId"] == 10


def test_custom_profile_story_favorite_requires_cloud_image_path(tmp_path: Path) -> None:
    renderer = _make_renderer(
        tmp_path,
        resources={
            "storyFavoriteResources": {
                "event_story:10": {
                    "title": "First",
                    "bannerPath": "asset/cn-assets/startapp/event_story/event_test/screen_image/banner_event_story.png",
                }
            }
        },
    )

    assert renderer.story_favorite_image_path({"storyType": "event_story", "storyId": 10}) is None


def test_custom_profile_render_request_decodes_resources() -> None:
    card, context, resources = decode_custom_profile_render_request(
        {
            "card": {"seq": 1},
            "profile_context": {"user": {"userId": 1}},
            "resources": {"storyFavoriteResources": {"event_story:10": {"imagePath": "asset/path.png"}}},
        }
    )

    assert card["seq"] == 1
    assert context["user"]["userId"] == 1
    assert resources["storyFavoriteResources"]["event_story:10"]["imagePath"] == "asset/path.png"


def test_custom_profile_honor_transform_keeps_native_canvas(tmp_path: Path) -> None:
    layer = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    layer.putpixel((10, 10), (255, 0, 0, 255))
    (tmp_path / "fonts").mkdir()
    (tmp_path / "asset" / "cn-assets" / "startapp" / "custom_profile").mkdir(parents=True)
    renderer = PNGRenderer(
        masterdata=None,
        assets=tmp_path / "asset" / "cn-assets" / "startapp" / "custom_profile",
        fonts=tmp_path / "fonts",
        resources={},
        tmp_font_metadata=None,
        shape_sprite_dir=None,
        unity_ui_sprite_dir=None,
        profile_context={},
        region="cn",
        position_scale=1.0,
        clip_canvas_transform=False,
    )

    prepared = renderer.prepare_transformed_layer(
        (layer, (10, 10)),
        {"position": {"x": 0, "y": 0}, "rotation": {"z": 0}, "scale": {"x": 1, "y": 1}},
        "bonds_honor",
    )

    assert prepared is not None
    assert prepared.image.size == (20, 20)


def test_custom_profile_general_deck_card_uses_deck_cutout_art(tmp_path: Path) -> None:
    _write_png(
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_cutout"
        / "res010_no034"
        / "after_training.png",
        (330, 512),
    )
    _write_png(
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_cutout_trm"
        / "res010_no034"
        / "after_training.png",
        (330, 512),
    )
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userCards": [
                {
                    "cardId": 915,
                    "specialTrainingStatus": "done",
                    "defaultImage": "special_training",
                    "level": 60,
                    "masterRank": 5,
                }
            ],
        },
        resources={
            "cards": {915: {"id": 915, "assetbundleName": "res010_no034", "cardRarityType": "rarity_4"}},
            "cardAssets": {
                915: {
                    "id": 915,
                    "assetbundleName": "res010_no034",
                    "smallAfterTrainingPath": (
                        "asset/cn-assets/startapp/character/member_small/res010_no034/card_after_training.png"
                    ),
                    "deckAfterTrainingPath": (
                        "asset/cn-assets/startapp/character/member_cutout/res010_no034/after_training.png"
                    ),
                    "clipAfterTrainingPath": (
                        "asset/cn-assets/startapp/character/member_cutout_trm/res010_no034/after_training.png"
                    ),
                }
            },
        },
    )

    assert (
        renderer.card_image_path_for_state(915, True, "deck")
        .as_posix()
        .endswith("/character/member_cutout/res010_no034/after_training.png")
    )
    image = renderer.compose_profile_deck_card(915)
    assert image is not None
    assert image.size == (156, 242)
    assert image.getpixel((4, 4))[3] == 255


def test_custom_profile_general_deck_card_does_not_apply_slanted_mask(tmp_path: Path) -> None:
    deck_path = (
        tmp_path
        / "asset"
        / "cn-assets"
        / "startapp"
        / "character"
        / "member_cutout"
        / "res010_no034"
        / "after_training.png"
    )
    _write_png_color(deck_path, (330, 512), (255, 0, 0, 255))
    mask = Image.new("RGBA", (330, 512), (0, 0, 0, 255))
    for y in range(mask.height):
        for x in range(80):
            mask.putpixel((x, y), (0, 0, 0, 0))
    mask_path = tmp_path / "static_images" / "customprofile" / "tex_mask_card_s.png"
    mask_path.parent.mkdir(parents=True, exist_ok=True)
    mask.save(mask_path)
    renderer = _make_renderer(
        tmp_path,
        profile_context={
            "userCards": [
                {
                    "cardId": 915,
                    "level": 60,
                    "masterRank": 0,
                    "specialTrainingStatus": "done",
                    "defaultImage": "special_training",
                }
            ],
        },
        resources={
            "cards": {915: {"id": 915, "assetbundleName": "res010_no034", "cardRarityType": "rarity_4"}},
            "cardAssets": {
                915: {
                    "id": 915,
                    "assetbundleName": "res010_no034",
                    "deckAfterTrainingPath": (
                        "asset/cn-assets/startapp/character/member_cutout/res010_no034/after_training.png"
                    ),
                }
            },
        },
    )

    image = renderer.compose_profile_deck_card(915)

    assert image is not None
    assert image.getpixel((4, 4))[3] == 255


def test_custom_profile_card_master_rank_zero_is_not_drawn(tmp_path: Path) -> None:
    _write_png_color(tmp_path / "static_images" / "card" / "train_rank_0.png", (88, 88), (0, 255, 0, 255))
    renderer = _make_renderer(
        tmp_path,
        profile_context={"userCards": [{"cardId": 915, "level": 60, "masterRank": 0}]},
        resources={
            "cards": {915: {"id": 915, "assetbundleName": "res010_no034", "cardRarityType": "rarity_4"}},
        },
    )
    image = Image.new("RGBA", (330, 512), (255, 0, 0, 255))

    renderer.draw_deck_card_view_overlays(image, 915)

    assert image.getpixel((250, 8))[:3] == (255, 0, 0)


def test_custom_profile_unity_sprite_reuses_static_card_assets(tmp_path: Path) -> None:
    _write_png(tmp_path / "static_images" / "card" / "train_rank_0.png", (7, 6))
    _write_png(tmp_path / "static_images" / "card" / "attr_icon_cute.png", (8, 8))
    _write_png(tmp_path / "static_images" / "card" / "rare_star_after_training.png", (9, 7))
    _write_png(tmp_path / "static_images" / "card" / "frame_rarity_4.png", (10, 10))

    renderer = _make_renderer(tmp_path)

    assert renderer.unity_ui_sprite("masterRank_L_0").size == (7, 6)
    assert renderer.unity_ui_sprite("icon_attribute_cute_64").size == (8, 8)
    assert renderer.unity_ui_sprite("rarity_star_afterTraining").size == (9, 7)
    assert renderer.unity_ui_sprite("cardFrame_S_4").size == (10, 10)


def test_custom_profile_unity_sprite_loads_customprofile_static_assets(tmp_path: Path) -> None:
    _write_png(tmp_path / "static_images" / "customprofile" / "label_mark_leader_L_pk.png", (11, 5))

    renderer = _make_renderer(tmp_path)

    assert renderer.unity_ui_sprite("label_mark_leader_L_pk").size == (11, 5)


def test_custom_profile_preferred_percent_indent_resolves_finite_and_saturated_widths(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path, tmp_box_mode="preferred", tmp_preferred_padding_x=64.0)
    half_indent = renderer_mod.StyledLine([], replace(_base_tmp_style(), indent_percent=0.5))
    half_layout = SimpleNamespace(
        preferred_width=10.0,
        lines=[SimpleNamespace(styled_line=half_indent, width=30.0)],
        dominant_size=24.0,
        content_height=12.0,
    )

    assert renderer.tmp_resolve_percent_indent_margin_width(
        [half_indent], "font", tmp_path / "font.ttf", 24.0, 0.0, 24.0, 0.0, half_layout
    ) == pytest.approx(188.0)

    saturated_indent = renderer_mod.StyledLine([], replace(_base_tmp_style(), line_indent_percent=1.0))
    saturated_layout = SimpleNamespace(
        preferred_width=10.0,
        lines=[SimpleNamespace(styled_line=saturated_indent, width=30.0)],
        dominant_size=24.0,
        content_height=12.0,
    )
    assert renderer.tmp_preferred_percent_indent_margin_width(saturated_layout) == (
        renderer_mod.TMP_PERCENT_INDENT_MAX_MARGIN_WIDTH
    )
    assert (
        renderer.tmp_resolve_percent_indent_margin_width(
            [renderer_mod.StyledLine([], _base_tmp_style())],
            "font",
            tmp_path / "font.ttf",
            24.0,
            0.0,
            24.0,
            0.0,
            half_layout,
        )
        is None
    )


def test_custom_profile_percent_indent_fixed_point_converges(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path, tmp_box_mode="size")
    line = renderer_mod.StyledLine([], replace(_base_tmp_style(), indent_percent=0.5))
    zero_layout = SimpleNamespace(preferred_width=100.0, dominant_size=24.0, content_height=12.0)
    margin_widths: list[float] = []

    monkeypatch.setattr(renderer, "tmp_text_box_size", lambda _size, width, _height: (width, 10.0))

    def fake_layout(*args, **kwargs):
        margin_widths.append(args[8])
        preferred_width = 120.0
        return SimpleNamespace(preferred_width=preferred_width, dominant_size=24.0, content_height=12.0)

    monkeypatch.setattr(renderer, "tmp_native_text_layout", fake_layout)

    resolved = renderer.tmp_resolve_percent_indent_margin_width(
        [line], "font", tmp_path / "font.ttf", 24.0, 0.0, 24.0, 0.0, zero_layout
    )

    assert resolved == pytest.approx(120.0)
    assert margin_widths == [100.0, 120.0]


def test_custom_profile_layout_audit_helpers_preserve_optional_details(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path)
    style = _base_tmp_style()
    run = TextRun("A", style)
    line = renderer_mod.StyledLine([run], style)
    text_data = renderer_mod.TMPGeneratedTextData("A", 1, 0x0101, 24.0, 0.2, 2, 3, 1.0)
    mesh_state = renderer_mod.TMPUpdateMeshState("font", None, "A", 24.0, "#010203", 0x0101, 1.0, "#040506", 0.2)
    native_line_layout = SimpleNamespace(baselines=[4.0], max_ascender=8.0, max_descender=-2.0, content_height=10.0)
    native_text_layout = SimpleNamespace(current_em_scale=0.24, marker="native")
    mesh_text_layout = SimpleNamespace(marker="mesh")
    measure = renderer_mod.TMPRunMeasure(5.0, -1.0, 4.0, -2.0, 7.0)
    monkeypatch.setattr(renderer_mod, "load_font", lambda *_: object())
    monkeypatch.setattr(renderer, "measure_tmp_run", lambda *_: measure)
    monkeypatch.setattr(renderer, "tmp_character_spacing_advance", lambda *_: 0.5)
    monkeypatch.setattr(renderer, "tmp_run_glyph_audit", lambda *_: [{"char": "A"}])
    monkeypatch.setattr(
        renderer,
        "tmp_native_text_layout_audit_dict",
        lambda layout, **_kwargs: {"marker": layout.marker},
    )

    renderer.record_tmp_layout_audit(
        {"id": 9, "objectData": {"layer": 7}},
        text_data,
        mesh_state,
        [(line, [(run, 2.0, 5.0)], 3.0, 10.0, 5.0)],
        "font",
        tmp_path / "font.ttf",
        5.0,
        10.0,
        10.0,
        10.0,
        20.0,
        30.0,
        native_line_layout,
        [4.0],
        native_text_layout,
        mesh_text_layout,
        (-1.0, -2.0, 6.0, 8.0),
        (11.0, 12.0),
        (20, 30),
    )

    audit = renderer.tmp_layout_audit[-1]
    assert audit["layer"] == 7
    assert audit["lines"][0]["nativeBaselineDown"] == 4.0
    assert audit["lines"][0]["runs"][0]["visualBounds"] == {
        "left": -1.0,
        "right": 4.0,
        "top": -2.0,
        "bottom": 7.0,
    }
    assert audit["layout"]["meshPixelBounds"] == {"left": -1.0, "top": -2.0, "right": 6.0, "bottom": 8.0}
    assert audit["layout"]["localImage"] == {
        "width": 20,
        "height": 30,
        "rectOriginX": 11.0,
        "rectOriginY": 12.0,
    }
    assert audit["layout"]["nativeTextInfo"] == {"marker": "native"}
    assert audit["layout"]["meshNativeTextInfo"] == {"marker": "mesh"}

    empty_metadata = renderer.tmp_layout_audit_metadata(
        1.0, 2.0, 3.0, 4.0, 5.0, 6.0, None, None, None, None, None, None, None
    )
    assert all(
        empty_metadata[key] is None
        for key in ("meshPixelBounds", "localImage", "nativeLineLayout", "nativeTextInfo", "meshNativeTextInfo")
    )


def test_custom_profile_tmp_shader_helpers_preserve_material_semantics(tmp_path: Path) -> None:
    renderer = _make_renderer(tmp_path)
    asset = SimpleNamespace(
        gradient_scale=6.0,
        face_dilate=0.1,
        outline_width=0.2,
        outline_softness=0.3,
        weight_normal=0.4,
        weight_bold=0.8,
        underlay_offset_x=2.0,
        underlay_offset_y=-1.0,
        underlay_softness=0.5,
        glow_offset=0.25,
        glow_outer=0.75,
        sharpness=0.25,
        scale_ratio_a=0.9,
        scale_ratio_b=0.8,
        scale_ratio_c=0.7,
    )

    assert renderer.tmp_shader_ratios(asset, 0.4, has_ratios_keyword=True) == (0.9, 0.8, 0.7)
    ratio_a, ratio_b, ratio_c = renderer.tmp_shader_ratios(asset, 0.4, has_underlay=True, has_glow=True)
    assert ratio_a == pytest.approx(5.0 / 6.0)
    assert ratio_b == pytest.approx(3.5 / 6.0)
    assert ratio_c == pytest.approx(3.5 / (6.0 * 2.9))
    assert renderer.tmp_shader_material(None).scale_ratio_c == 1.0
    assert renderer.tmp_sdf_field_shift(0.49, -0.49) == (0, 0)
    assert renderer.tmp_sdf_field_shift(2.5, -1.5) == (2, -2)

    plain = renderer.tmp_sdf_shading_scalars(None, _base_tmp_style(), "#112233", 0.0, sdf_scale=2.0)
    assert plain.underlay is None
    shaded = renderer.tmp_sdf_shading_scalars(asset, _base_tmp_style(), "#112233", 0.4, sdf_scale=2.0)
    assert shaded.face_color == (0, 0, 0)
    assert shaded.underlay is not None
    assert shaded.underlay.color == (17, 34, 51)
    expected_offset_x = -asset.underlay_offset_x * ratio_c * asset.gradient_scale
    expected_offset_y = -asset.underlay_offset_y * ratio_c * asset.gradient_scale
    assert (shaded.underlay.shift_x, shaded.underlay.shift_y) == renderer.tmp_sdf_field_shift(
        expected_offset_x, expected_offset_y
    )


def test_custom_profile_static_atlas_run_reuses_placement_and_field_helpers(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path, tmp_scale_mode="x")
    style = replace(_base_tmp_style(), scale_x=2.0)
    metrics = replace(
        _glyph_metrics(width=2.0, height=2.0, bearing_x=0.0, bearing_y=2.0, advance=3.0),
        rect_w=2,
        rect_h=2,
    )
    atlas_path = tmp_path / "atlas.png"
    asset = SimpleNamespace(
        point_size=10.0,
        ascent_line=3.0,
        descent_line=-1.0,
        glyphs={ord("A"): metrics, ord(" "): metrics},
        atlas_paths=[atlas_path],
    )
    monkeypatch.setattr(renderer, "tmp_static_sdf_asset", lambda *_: asset)
    monkeypatch.setattr(renderer, "tmp_character_spacing_advance", lambda *_: 0.0)
    monkeypatch.setattr(renderer, "tmp_display_padding", lambda *_: 1)
    monkeypatch.setattr(renderer, "tmp_atlas_alpha", lambda *_: Image.new("L", (8, 8), 255))
    monkeypatch.setattr(
        renderer,
        "shade_tmp_sdf_field",
        lambda field, *_: Image.new("RGBA", (field.shape[1], field.shape[0]), (255, 255, 255, 255)),
    )

    rendered = renderer.render_tmp_static_atlas_run("font", TextRun("AA", style), 20.0, "#000000", 0.0)

    assert rendered is not None
    image, bbox, pad = rendered
    assert image.size == (28, 10)
    assert bbox == (0, 0, 12, 8)
    assert pad == 1
    assert renderer.tmp_static_atlas_placements("font", TextRun(" ", style), 20.0, asset) is None
    assert renderer.tmp_static_atlas_placements("font", TextRun("B", style), 20.0, asset) is None


def test_custom_profile_render_text_uses_measured_pillow_layout(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path, text_layout="pil", text_pivot="left", tmp_font_scale=1.0)
    text_data = renderer_mod.TMPGeneratedTextData("AB", 1, 0x0101, 24.0, 0.0, 0, 0, 0.0)
    mesh_state = renderer_mod.TMPUpdateMeshState("font", None, "AB", 24.0, "#112233", 0x0101, 0.0, "#445566", 0.0)
    draws: list[tuple[float, float, float]] = []
    monkeypatch.setattr(renderer, "generate_text_data", lambda _item: text_data)
    monkeypatch.setattr(renderer, "update_text_mesh_state", lambda *_: mesh_state)
    monkeypatch.setattr(renderer, "font_path_for", lambda *_: tmp_path / "font.ttf")
    monkeypatch.setattr(renderer_mod, "load_font", lambda *_: object())
    monkeypatch.setattr(
        renderer,
        "measure_tmp_run",
        lambda *_: renderer_mod.TMPRunMeasure(10.0, -1.0, 9.0, -2.0, 8.0),
    )
    monkeypatch.setattr(renderer, "tmp_character_spacing_advance", lambda *_: 1.0)
    monkeypatch.setattr(
        renderer,
        "draw_run",
        lambda _img, _font_name, _font_path, _run, x, y, line_h, *_rest: draws.append((x, y, line_h)),
    )

    rendered = renderer.render_text({})

    assert rendered is not None
    image, pivot = rendered
    assert image.width > 10
    assert pivot == (renderer.text_pad(24.0, 0), image.height / 2)
    assert draws == [(renderer.text_pad(24.0, 0), renderer.text_pad(24.0, 0), 24.0)]

    monkeypatch.setattr(renderer, "generate_text_data", lambda _item: replace(text_data, text=" "))
    assert renderer.render_text({}) is None


def test_custom_profile_tmp_text_box_delegates_layout_and_drawing(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path)
    style = _base_tmp_style()
    line = renderer_mod.StyledLine([TextRun("A", style)], style)
    text_data = renderer_mod.TMPGeneratedTextData("A", 1, 0x0101, 24.0, 0.0, 0, 0, 0.0)
    mesh_state = renderer_mod.TMPUpdateMeshState("font", None, "A", 24.0, "#112233", 0x0101, 0.0, "#445566", 0.0)
    native_line_layout = SimpleNamespace(baselines=[0.0], max_ascender=8.0, max_descender=-2.0, content_height=10.0)
    native_layout = SimpleNamespace(
        dominant_size=24.0,
        preferred_width=20.0,
        preferred_height=10.0,
        content_height=10.0,
    )
    mesh_line = SimpleNamespace(
        styled_line=line,
        run_metrics=[(line.runs[0], 0.0, 5.0)],
        y_down=0.0,
        line_height=10.0,
        width=5.0,
    )
    mesh_layout = SimpleNamespace(
        lines=[mesh_line],
        line_layout=native_line_layout,
        accumulated_line_height=10.0,
    )
    draw_calls: list[tuple[float, float]] = []
    audits: list[tuple] = []
    monkeypatch.setattr(renderer, "generate_text_data", lambda _item: text_data)
    monkeypatch.setattr(renderer, "update_text_mesh_state", lambda *_: mesh_state)
    monkeypatch.setattr(renderer, "resolve_tmp_text_box_layouts", lambda *_: (native_layout, mesh_layout))
    monkeypatch.setattr(renderer, "tmp_text_box_size", lambda *_: (20.0, 10.0))
    monkeypatch.setattr(renderer, "tmp_native_baseline_downs", lambda *_: [5.0])
    monkeypatch.setattr(renderer, "tmp_native_mesh_pixel_bounds", lambda *_: (0.0, 0.0, 20.0, 10.0))
    monkeypatch.setattr(renderer, "record_tmp_layout_audit", lambda *args: audits.append(args))
    monkeypatch.setattr(
        renderer,
        "draw_tmp_text_box_content",
        lambda _image, _font_name, _font_path, _layout, _baselines, _align, _box_w, x, y, *_rest: draw_calls.append(
            (x, y)
        ),
    )

    rendered = renderer.render_tmp_text_box({}, "font", tmp_path / "font.ttf", style, [line])

    assert rendered is not None
    image, pivot = rendered
    pad = renderer.text_pad(24.0, 0)
    assert image.size == (20 + pad * 2, 10 + pad * 2)
    assert pivot == (pad + 10.0, pad + 5.0)
    assert draw_calls == [(pad, pad)]
    assert len(audits) == 1


def test_custom_profile_tmp_text_box_layout_resolution_reflows_percent_indent(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path)
    style = replace(_base_tmp_style(), indent_percent=0.5)
    line = renderer_mod.StyledLine([], style)
    preferred = SimpleNamespace(marker="preferred")
    reflowed = SimpleNamespace(marker="reflowed")
    mesh = SimpleNamespace(marker="mesh")
    calls: list[tuple[str, float | None]] = []
    source_metric_flags: list[bool] = []

    def fake_layout(*args, **kwargs):
        calls.append((args[6], args[8]))
        source_metric_flags.append(kwargs["source_metrics_only"])
        if args[6] == "mesh":
            return mesh
        return preferred if args[8] is None else reflowed

    def fake_margin(*_args, **kwargs):
        source_metric_flags.append(kwargs["source_metrics_only"])
        return 40.0

    monkeypatch.setattr(renderer, "tmp_native_text_layout", fake_layout)
    monkeypatch.setattr(renderer, "tmp_resolve_percent_indent_margin_width", fake_margin)

    resolved = renderer.resolve_tmp_text_box_layouts(
        [line],
        "font",
        tmp_path / "font.ttf",
        24.0,
        0.0,
        24.0,
        0.0,
        source_metrics_only=True,
    )

    assert resolved == (reflowed, mesh)
    assert calls == [("preferred", None), ("preferred", 40.0), ("mesh", 40.0)]
    assert source_metric_flags == [True, True, True, True]


def test_custom_profile_tmp_native_visual_metrics_preserve_measurement_paths(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path)
    style = _base_tmp_style()
    run = TextRun("A", style)
    source_block = SimpleNamespace(advance=11.0, bearing_x=-2.0, width=7.0)
    monkeypatch.setattr(renderer, "use_em_block", lambda *_: True)
    monkeypatch.setattr(renderer, "tmp_source_block_metrics", lambda *_: source_block)
    monkeypatch.setattr(renderer, "tmp_native_style_extents", lambda *_: (8.0, -3.0))

    assert renderer.tmp_native_run_visual_metrics(run, "font", tmp_path / "font.ttf", 24.0, 1.0) == (
        renderer_mod.TMPRunVisualMetrics(11.0, -2.0, 5.0, -8.0, 3.0)
    )

    measured = SimpleNamespace(
        advance=9.0,
        visual_left=-1.0,
        visual_right=8.0,
        visual_top=-6.0,
        visual_bottom=2.0,
    )
    monkeypatch.setattr(renderer, "use_em_block", lambda *_: False)
    monkeypatch.setattr(renderer, "measure_tmp_source_run", lambda *_: measured)
    monkeypatch.setattr(renderer_mod, "load_font", lambda *_: pytest.fail("source metrics must not load a font"))

    assert renderer.tmp_native_run_visual_metrics(
        run,
        "font",
        tmp_path / "font.ttf",
        24.0,
        1.0,
        source_metrics_only=True,
    ) == renderer_mod.TMPRunVisualMetrics(9.0, -1.0, 8.0, -6.0, 2.0)


def test_custom_profile_tmp_native_padded_bounds_preserve_fx_and_scale_modes(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path, tmp_scale_mode="fx-native")
    style = _base_tmp_style()
    visual = renderer_mod.TMPRunVisualMetrics(10.0, -2.0, 8.0, -7.0, 3.0)
    monkeypatch.setattr(renderer, "tmp_native_fx_quad", lambda *_: (4.0, 0.0, -3.0, 0.0, 9.0, 0.0, 1.0, 0.0))

    assert renderer.tmp_native_padded_horizontal_bounds(visual, style, 1.0, 2.0) == (-3.0, 9.0)

    renderer.tmp_scale_mode = "x"
    monkeypatch.setattr(renderer, "tmp_scale_x_bounds", lambda left, right, scale: (left * scale, right * scale))
    assert renderer.tmp_native_padded_horizontal_bounds(visual, style, 1.0, 2.0) == (-6.0, 18.0)


def test_custom_profile_dynamic_glyph_bounds_support_freetype_and_pillow(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path)
    mask = Image.new("L", (2, 3), 255)
    metrics = SimpleNamespace(bearing_x=-2.2, bearing_y=5.1, width=5.6, height=7.2)
    ft = SimpleNamespace(glyph_bitmap=lambda *_: (mask, -1, 4, metrics))

    assert renderer.tmp_dynamic_glyph_bounds(ft, tmp_path / "font.ttf", "A", 24.0) == (
        (-3, -6, 4, 3),
        mask,
        -1,
        4,
    )

    monkeypatch.setattr(renderer_mod, "load_font", lambda *_: SimpleNamespace(getbbox=lambda _char: (1, 2, 3, 4)))
    assert renderer.tmp_dynamic_glyph_bounds(None, tmp_path / "font.ttf", "A", 24.0) == (
        (1, 2, 3, 4),
        None,
        0,
        0,
    )


def test_custom_profile_dynamic_glyph_sdf_stores_built_result(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path)
    source_path = tmp_path / "font.ttf"
    asset = SimpleNamespace(name="asset", point_size=24.0, gradient_scale=5.0, atlas_padding=2.0)
    cached = renderer_mod.TMPDynamicGlyphSDF(Image.new("L", (2, 2), 255), (0, 0, 2, 2), 1, 24.0)
    key = (str(source_path), "asset", "A", 24.0)
    l2_key = ("l2",)
    stores: list[tuple] = []
    monkeypatch.setattr(renderer, "tmp_dynamic_glyph_source", lambda *_: (asset, source_path, 24.0, "A"))
    monkeypatch.setattr(renderer, "tmp_dynamic_glyph_cache_keys", lambda *_: (key, l2_key))
    monkeypatch.setattr(renderer, "tmp_cached_dynamic_glyph", lambda *_: (False, None))
    monkeypatch.setattr(renderer, "build_tmp_dynamic_glyph_sdf", lambda *_: cached)
    monkeypatch.setattr(renderer, "_store_dynamic_glyph", lambda *args: stores.append(args))

    assert renderer.tmp_dynamic_glyph_sdf("font", source_path, "A") == (cached, asset)
    assert stores == [(key, l2_key, cached)]


def test_custom_profile_direct_sdf_quad_prepares_all_field_kinds(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path)
    style = _base_tmp_style()
    plan = renderer_mod.TMPFieldWarpPlan((1.0, 0.0, 0.0, 0.0, 1.0, 0.0), (3, 4), 5, 6)
    scalars = renderer_mod.TMPSdfShadingScalars(1.0, 0.0, 1.0, (255, 255, 255), None)
    object_data: dict = {}
    geometry = (2, 2)
    common = (None, style, 1.0, 2.0, geometry, None)
    monkeypatch.setattr(renderer, "tmp_sdf_field_warp_plan", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(renderer, "tmp_sdf_shading_scalars", lambda *_: scalars)

    dynamic = TMPDynamicFontField(tmp_path / "font.ttf", ord("A"), 24.0, (0, 0, 2, 2), 1, 1, (2, 2), 4.9)
    dynamic_quad, dynamic_bytes = renderer.prepare_direct_sdf_quad(
        (dynamic, *common),
        (0.0, 0.0),
        object_data,
        "#000000",
        0.0,
        4,
    )
    assert isinstance(dynamic_quad, renderer_mod.DirectSdfFontQuad)
    assert dynamic_quad.size == (3, 4)
    assert dynamic_bytes == 16

    atlas = TMPStaticAtlasField(tmp_path / "atlas.png", (8, 8), (0, 0, 2, 2), (2, 2))
    atlas_quad, atlas_bytes = renderer.prepare_direct_sdf_quad(
        (atlas, *common),
        (0.0, 0.0),
        object_data,
        "#000000",
        0.0,
        4,
    )
    assert isinstance(atlas_quad, renderer_mod.DirectSdfAtlasQuad)
    assert atlas_quad.crop == (0, 0, 2, 2)
    assert atlas_bytes == 16

    warped = Image.new("L", (3, 4), 255)
    monkeypatch.setattr(renderer, "warp_tmp_sdf_field_direct", lambda *_args, **_kwargs: (warped, 7, 8))
    raster_quad, raster_bytes = renderer.prepare_direct_sdf_quad(
        (Image.new("L", (2, 2), 255), *common),
        (0.0, 0.0),
        object_data,
        "#000000",
        0.0,
        4,
    )
    assert isinstance(raster_quad, renderer_mod.DirectSdfQuad)
    assert (raster_quad.left, raster_quad.top) == (7, 8)
    assert raster_bytes == 16


def test_custom_profile_dynamic_sdf_run_composes_scaled_glyphs(tmp_path: Path, monkeypatch) -> None:
    renderer = _make_renderer(tmp_path, tmp_scale_mode="x")
    style = replace(_base_tmp_style(), scale_x=2.0, mspace=6.0)
    gate_asset = SimpleNamespace(atlas_population_mode=1)
    cached = renderer_mod.TMPDynamicGlyphSDF(Image.new("L", (2, 2), 255), (0, 0, 2, 2), 1, 10.0)
    monkeypatch.setattr(renderer, "tmp_sdf_asset", lambda *_: gate_asset)
    monkeypatch.setattr(renderer_mod, "load_font", lambda *_: object())
    monkeypatch.setattr(renderer, "glyph_advance", lambda *_: 4.0)
    monkeypatch.setattr(renderer, "tmp_render_glyph_char", lambda _font, char, _size: char)
    monkeypatch.setattr(renderer, "tmp_dynamic_glyph_sdf", lambda *_: (cached, None))
    monkeypatch.setattr(renderer, "tmp_character_spacing_advance", lambda *_: 1.0)
    monkeypatch.setattr(
        renderer,
        "shade_tmp_sdf_field",
        lambda field, *_: Image.new("RGBA", (field.shape[1], field.shape[0]), (255, 255, 255, 255)),
    )

    rendered = renderer.render_tmp_dynamic_sdf_run_from_glyphs(
        "font", tmp_path / "font.ttf", TextRun("AB", style), 20.0, "#000000", 0.0
    )

    assert rendered is not None
    image, bbox, pad = rendered
    assert image.size == (34, 8)
    assert bbox == (0, 0, 26, 4)
    assert pad == 2


def test_custom_profile_region_path_expands_region_placeholder(tmp_path: Path) -> None:
    target = tmp_path / "asset" / "jp-assets" / "startapp" / "custom_profile"
    target.mkdir(parents=True)

    assert (
        _require_region_path(
            "custom_profile_assets_dir",
            tmp_path / "asset" / "{region}-assets" / "startapp" / "custom_profile",
            "jp",
        )
        == target
    )


def test_custom_profile_region_path_replaces_literal_region_segment(tmp_path: Path) -> None:
    target = tmp_path / "fonts" / "jp"
    target.mkdir(parents=True)

    assert _require_region_path("custom_profile_fonts_dir", tmp_path / "fonts" / "cn", "jp") == target
    assert _region_path_candidates(tmp_path / "asset" / "cn-assets" / "startapp" / "custom_profile", "jp")[0] == (
        tmp_path / "asset" / "jp-assets" / "startapp" / "custom_profile"
    )


def test_custom_profile_tmp_font_metadata_is_optional(tmp_path: Path) -> None:
    path = tmp_path / "custom_profile" / "tmp-font-assets" / "{region}" / "metadata.json"

    assert _optional_region_file("custom_profile_tmp_font_metadata", path, "cn") is None


def test_custom_profile_api_uses_cropped_profile_viewport(tmp_path: Path, monkeypatch) -> None:
    assets = tmp_path / "asset" / "cn-assets" / "startapp" / "custom_profile"
    fonts = tmp_path / "fonts" / "cn"
    shape_sprites = tmp_path / "shape-sprites"
    ui_sprites = tmp_path / "unity-ui-sprites"
    for path in (assets, fonts, shape_sprites, ui_sprites):
        path.mkdir(parents=True)
    captured: dict[str, object] = {}

    class FakePNGRenderer:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def render_card(self, card: dict) -> Image.Image:
            return Image.new("RGBA", (1, 1), (0, 0, 0, 0))

    monkeypatch.setattr(
        custom_profile_drawer,
        "CUSTOM_PROFILE_ASSETS_DIR",
        tmp_path / "asset" / "{region}-assets" / "startapp" / "custom_profile",
    )
    monkeypatch.setattr(custom_profile_drawer, "CUSTOM_PROFILE_FONTS_DIR", tmp_path / "fonts" / "{region}")
    monkeypatch.setattr(custom_profile_drawer, "CUSTOM_PROFILE_SHAPE_SPRITE_DIR", shape_sprites)
    monkeypatch.setattr(custom_profile_drawer, "CUSTOM_PROFILE_UNITY_UI_SPRITE_DIR", ui_sprites)
    monkeypatch.setattr(custom_profile_drawer, "CUSTOM_PROFILE_TMP_FONT_METADATA", None)
    monkeypatch.setattr(custom_profile_drawer, "PNGRenderer", FakePNGRenderer)

    image = custom_profile_drawer._render_custom_profile_card_sync({"customProfileCard": {}}, {}, {}, "cn")

    assert image.size == (1, 1)
    assert captured["canvas_w"] == 2048
    assert captured["canvas_h"] == 909
    assert captured["origin_x"] == 1024.0
    assert captured["origin_y"] == 454.5
