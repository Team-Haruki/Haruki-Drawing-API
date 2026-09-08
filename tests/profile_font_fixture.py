"""Small, public-font TMP fixture; no captured player data or game assets."""

import json
from pathlib import Path
import shutil
from types import SimpleNamespace


def build_tmp_fixture(directory):
    import matplotlib

    directory.mkdir(parents=True, exist_ok=True)
    font = directory / "Fixture.ttf"
    shutil.copyfile(Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSans.ttf", font)
    names = ("Fixture", "FOT-RodinNTLGPro-EB-OnDemand", "FOT-RodinNTLGPro-DB")
    metadata = directory / "metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "materials": [{"path_id": 1, "floats": {"_GradientScale": 6, "_ScaleRatioA": 1, "_ScaleRatioC": 1}}],
                "tmp_font_assets": [
                    {
                        "name": name,
                        "bundle": "fixture",
                        "material": 1,
                        "source_font_data_path": font.name,
                        "atlas_population_mode": 1,
                        "atlas_width": 256,
                        "atlas_height": 256,
                        "atlas_padding": 5,
                        "face_info": {
                            "m_PointSize": 90,
                            "m_Scale": 1,
                            "m_LineHeight": 105,
                            "m_AscentLine": 84,
                            "m_DescentLine": -21,
                            "m_TabWidth": 28,
                        },
                    }
                    for name in names
                ],
            }
        )
    )
    # A synthetic signed-distance square exercises static atlas sampling without
    # copying any proprietary TMP atlas or glyph table.
    from PIL import Image

    atlas_dir = directory / "atlases"
    atlas_dir.mkdir(exist_ok=True)
    atlas = Image.new("L", (64, 64))
    atlas.putdata(
        [max(0, min(255, round(128 + min(x - 12, 51 - x, y - 12, 51 - y) * 16))) for y in range(64) for x in range(64)]
    )
    atlas.save(atlas_dir / "fixture_7.png")
    (directory / "chars.json").write_text(
        json.dumps([{"m_Unicode": ord(char), "m_GlyphIndex": 1, "m_Scale": 1} for char in "AB "])
    )
    (directory / "glyphs.json").write_text(
        json.dumps(
            [
                {
                    "m_Index": 1,
                    "m_Metrics": {
                        "m_Width": 40,
                        "m_Height": 40,
                        "m_HorizontalBearingX": 0,
                        "m_HorizontalBearingY": 40,
                        "m_HorizontalAdvance": 45,
                    },
                    "m_GlyphRect": {"m_X": 12, "m_Y": 12, "m_Width": 40, "m_Height": 40},
                    "m_AtlasIndex": 0,
                }
            ]
        )
    )
    data = json.loads(metadata.read_text())
    data["tmp_font_assets"].append(
        {
            **data["tmp_font_assets"][0],
            "name": "Static",
            "atlas_population_mode": 0,
            "atlas_width": 64,
            "atlas_height": 64,
            "atlas_textures": [7],
            "character_table_path": "chars.json",
            "glyph_table_path": "glyphs.json",
            "fallback_font_asset_names": ["Fixture"],
        }
    )
    metadata.write_text(json.dumps(data))
    resources = {
        "customProfileTextFonts": [{"id": 1, "fontName": "Fixture"}, {"id": 2, "fontName": "Static"}],
        "customProfileTextColors": [{"id": 1, "colorCode": "#33aaee"}, {"id": 2, "colorCode": "#ffdd44"}],
    }
    payload = directory / "request.json"
    payload.write_text(json.dumps({"resources": resources}))
    return SimpleNamespace(directory=directory, font=font, metadata=metadata, resources=resources, payload=payload)
