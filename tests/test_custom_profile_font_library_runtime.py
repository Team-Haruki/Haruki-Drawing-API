from __future__ import annotations

import json
from pathlib import Path

from src.sekai.profile.custom_profile import renderer


def _metrics(*, advance=8.0) -> renderer.TMPGlyphMetrics:
    return renderer.TMPGlyphMetrics(
        width=6.0,
        height=7.0,
        bearing_x=1.0,
        bearing_y=6.0,
        advance=advance,
        rect_x=1,
        rect_y=2,
        rect_w=3,
        rect_h=4,
        glyph_scale=1.0,
        atlas_index=0,
    )


def _asset(
    name: str,
    *,
    bundle: str = "bundle",
    source_font_path: Path | None = None,
    glyphs: dict[int, renderer.TMPGlyphMetrics] | None = None,
    fallback_names: list[str] | None = None,
    point_size: float = 10.0,
) -> renderer.TMPFontAsset:
    return renderer.TMPFontAsset(
        name=name,
        bundle=bundle,
        source_font_path=source_font_path,
        atlas_paths=[],
        atlas_population_mode=0,
        atlas_width=128,
        atlas_height=128,
        atlas_padding=5,
        point_size=point_size,
        face_scale=2,
        line_height=12,
        ascent_line=8,
        descent_line=-2,
        tab_width=4,
        gradient_scale=6,
        weight_normal=0,
        weight_bold=0.75,
        face_dilate=0,
        outline_width=0,
        outline_softness=0,
        sharpness=0,
        normal_spacing_offset=1,
        bold_spacing=2,
        scale_ratio_a=1,
        scale_ratio_b=1,
        scale_ratio_c=1,
        glow_offset=0,
        glow_outer=0,
        underlay_softness=0,
        underlay_offset_x=0,
        underlay_offset_y=0,
        fallback_names=fallback_names or [],
        glyphs=glyphs or {},
    )


def test_tmp_font_metadata_loading_covers_material_atlas_source_and_character_tables(tmp_path) -> None:
    source_font = tmp_path / "source.ttf"
    source_font.write_bytes(b"font")
    atlas_dir = tmp_path / "atlases"
    atlas_dir.mkdir()
    (atlas_dir / "font_7.png").write_bytes(b"png")
    (tmp_path / "chars.json").write_text(json.dumps([{"m_Unicode": 65, "m_GlyphIndex": 2, "m_Scale": 1.5}]))
    (tmp_path / "glyphs.json").write_text(
        json.dumps(
            [
                {
                    "m_Index": 2,
                    "m_Metrics": {
                        "m_Width": 6,
                        "m_Height": 7,
                        "m_HorizontalBearingX": 1,
                        "m_HorizontalBearingY": 6,
                        "m_HorizontalAdvance": 8,
                    },
                    "m_GlyphRect": {"m_X": 1, "m_Y": 2, "m_Width": 3, "m_Height": 4},
                    "m_AtlasIndex": 0,
                }
            ]
        )
    )
    metadata = {
        "materials": [
            {
                "path_id": 9,
                "floats": {
                    "_TextureWidth": 128,
                    "_TextureHeight": 64,
                    "_GradientScale": 6,
                    "_WeightBold": 0.8,
                },
            },
            "ignored",
        ],
        "tmp_font_assets": [
            {
                "name": "Main",
                "bundle": "custom_profile_font.bundle",
                "material": 9,
                "source_font_data_path": "source.ttf",
                "atlas_textures": [7, 999],
                "character_table_path": "chars.json",
                "glyph_table_path": "glyphs.json",
                "atlas_population_mode": 1,
                "face_info": {
                    "m_PointSize": 10,
                    "m_Scale": 2,
                    "m_LineHeight": 12,
                    "m_AscentLine": 8,
                    "m_DescentLine": -2,
                    "m_TabWidth": 4,
                },
                "fallback_font_asset_names": ["Fallback", ""],
            }
        ],
    }
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps(metadata))
    recorded: list[Path] = []
    assets = renderer.TMPFontLibrary._load_assets(metadata_path, record=recorded.append)
    asset = assets["Main"][0]
    assert asset.source_font_path == source_font
    assert asset.atlas_paths == [atlas_dir / "font_7.png"]
    assert asset.fallback_names == ["Fallback"]
    assert asset.glyphs[65].advance == 8
    assert metadata_path in recorded
    assert renderer.TMPFontLibrary._materials_by_path_id(metadata)["9"]["path_id"] == 9

    assert renderer.TMPFontLibrary.load(None).assets == {}
    assert renderer.TMPFontLibrary.load(tmp_path / "missing.json").assets == {}
    assert renderer.TMPFontLibrary._character_table_rows(tmp_path, {}) is None
    assert renderer.TMPFontLibrary._source_font_path(tmp_path, {}) is None


def test_tmp_font_asset_candidates_metrics_fallbacks_and_deduplication(tmp_path) -> None:
    font_file = tmp_path / "Main.ttf"
    font_file.write_bytes(b"font")
    main = _asset("Main", source_font_path=font_file, glyphs={65: _metrics()}, fallback_names=["Fallback"])
    duplicate = _asset("Main", source_font_path=font_file, glyphs={65: _metrics()}, fallback_names=["Fallback"])
    on_demand = _asset("Main-OnDemand", glyphs={66: _metrics(advance=9)})
    fallback = _asset("Fallback", glyphs={67: _metrics(advance=10)})
    fallback_on_demand = _asset("Fallback-OnDemand", glyphs={68: _metrics(advance=11)})
    assets = {
        "Main": [main, duplicate],
        "Main-OnDemand": [on_demand],
        "Fallback": [fallback],
        "Fallback-OnDemand": [fallback_on_demand],
    }
    library = renderer.TMPFontLibrary(assets)
    assert library.active_asset("missing") is None
    assert library.static_asset("missing") is None
    assert library.static_asset("Main") is main
    assert library.source_font_path("Main") == font_file
    assert library.source_font_path("missing") is None

    candidates = library.metric_asset_candidates("Main", True)
    assert [asset.name for asset in candidates] == ["Main", "Main-OnDemand", "Fallback", "Fallback-OnDemand"]
    assert library.metric_asset_candidates("missing", False) == []
    assert library.source_asset_candidates("Main", True) == candidates

    metrics = library.glyph_metrics("Main", "A", 20, False)
    assert metrics is not None
    assert metrics.advance == 16
    assert library.glyph_metrics("Main", "Z", 20, True) is None
    assert library.glyph_asset_for("Main", "", True) is None
    assert library.glyph_asset_for("Main", "C", True) == (fallback, fallback.glyphs[67])

    source_main = _asset("Main", bundle="source", source_font_path=font_file)
    source_fallback = _asset("Fallback", bundle="source-fallback", source_font_path=font_file)
    separate = renderer.TMPFontLibrary(assets, {"Main": [source_main, source_main], "Fallback": [source_fallback]})
    assert separate.source_asset_candidates("Main", True) == [source_main, source_fallback]


def test_tmp_runtime_font_resolution_source_metric_cache_and_scaled_metrics(tmp_path, monkeypatch) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    runtime_font = runtime_dir / "main.otf"
    runtime_font.write_bytes(b"font")
    missing_source = tmp_path / "gone.ttf"
    main = _asset("Main-OnDemand", source_font_path=missing_source, glyphs={65: _metrics()})
    library = renderer.TMPFontLibrary({"Main": [main]}, runtime_fonts_dir=runtime_dir)
    resolved_font = library.runtime_source_font_path(main)
    assert resolved_font is not None
    assert resolved_font.name.lower() == runtime_font.name

    no_runtime = renderer.TMPFontLibrary({"Main": [main]})
    assert no_runtime.runtime_source_font_path(main) is None
    assert no_runtime.source_glyph_metrics("Main", "", 10) is None
    assert no_runtime.source_glyph_metrics("Main", "A", 10) is None

    calls: list[tuple[Path, str, float]] = []

    def fake_load(path, ch, size):
        calls.append((path, ch, size))
        return _metrics()

    monkeypatch.setattr(library, "_load_source_glyph_metrics", fake_load)
    base = library._source_glyph_metrics_for_asset(main, "A", 10)
    assert base is not None
    assert base.advance == 8
    scaled = library._source_glyph_metrics_for_asset(main, "A", 20)
    assert scaled is not None
    assert scaled.advance == 16
    assert library._source_glyph_metrics_for_asset(main, "A", 20) is scaled
    assert calls == [(resolved_font, "A", 10)]

    monkeypatch.setattr(library, "_load_source_glyph_metrics", lambda *_args: (_ for _ in ()).throw(RuntimeError()))
    assert library._source_glyph_metrics_for_asset(main, "B", 10) is None
    assert library._source_glyph_metrics_for_asset(main, "B", 20) is None


def test_tmp_font_vertical_and_spacing_metrics_cover_absent_invalid_and_valid_assets() -> None:
    main = _asset("Main")
    invalid = _asset("Invalid", point_size=0)
    library = renderer.TMPFontLibrary({"Main": [main], "Invalid": [invalid]})

    assert library.line_height("missing", 20, 2, False) is None
    assert library.line_height("Main", 20, 2, False) == 24
    assert library.line_height("Main", 20, 2, True) == 24
    assert library.face_extents("missing", 20, 1) is None
    assert library.face_extents("Main", 20, 1) == (16, -4)
    assert library.em_scale("missing", 20, 1) is None
    assert library.em_scale("Main", 20, 1) == 2
    assert library.tab_advance("missing", 20) is None
    assert library.tab_advance("Main", 20) == 8
    assert library.bold_spacing_advance("missing", 20) == 0
    assert library.bold_spacing_advance("Main", 20) == 4
    assert library.normal_spacing_advance("missing", 20) == 0
    assert library.normal_spacing_advance("Main", 20) == 2
    assert library.line_height("Invalid", 20, 1, False) is None
