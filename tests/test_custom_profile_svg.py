from dataclasses import replace
import json
import math
from pathlib import Path
import sys

from PIL import Image
import pytest

from src.sekai.profile.custom_profile.svg import (
    INVALID_TMP_TAG,
    Renderer,
    TextBreak,
    TextRun,
    TextStyle,
    apply_tmp_tag,
    color_or,
    css_url,
    file_uri,
    font_path,
    is_tmp_hash_color,
    load_index,
    main,
    normalize_color_hex,
    parse_float,
    parse_hex_alpha,
    parse_relaxed_float,
    parse_tmp_numeric,
    parse_tmp_percent,
    parse_tmp_position,
    parse_tmp_scale,
    parse_tmp_text,
    png_size,
    restore_tmp_tag_kind,
    select_cards,
    split_runs_by_line,
    strip_tmp_quotes,
    svg_escape,
    svg_href,
    tmp_anchor,
    tmp_color_alpha,
    tmp_hex_to_int,
    tmp_tag_kind,
    transform_attr,
    unity_point,
    unity_rotation_degrees,
)


def _style() -> TextStyle:
    return TextStyle(
        color="#112233",
        alpha=0.75,
        size=20.0,
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


@pytest.mark.parametrize(
    ("tag", "field", "expected"),
    [
        ("color=#abcdef80", "color", "#abcdef"),
        ("alpha=80", "alpha", pytest.approx(128 / 255)),
        ("size=150%", "size", 30.0),
        ("scale=250%", "scale_x", 2.5),
        ("cspace=0.5em", "cspace", 10.0),
        ("mspace=24px", "mspace", 24.0),
        ("indent=25%", "indent_percent", 0.25),
        ("line-indent=4px", "line_indent", 4.0),
        ("line-height=1.5em", "line_height", 30.0),
        ("rotate=12.5", "rotate", 12.5),
        ("voffset=-25%", "voffset", -5.0),
        ("pos=35%", "pos_percent", 0.35),
        ("mark=#fedcba", "mark_color", "#fedcba"),
    ],
)
def test_tmp_value_tags_preserve_supported_attributes(tag: str, field: str, expected: object) -> None:
    result = apply_tmp_tag(tag, _style())

    assert isinstance(result, TextStyle)
    assert getattr(result, field) == expected


@pytest.mark.parametrize(
    ("tag", "field", "expected"),
    [
        ("b", "bold", True),
        ("i", "italic", True),
        ("u", "underline", True),
        ("s", "strike", True),
        ("sup", "size", 10.0),
        ("sub", "voffset", -2.4),
    ],
)
def test_tmp_simple_tags_preserve_supported_attributes(tag: str, field: str, expected: object) -> None:
    result = apply_tmp_tag(tag, _style())

    assert isinstance(result, TextStyle)
    actual = getattr(result, field)
    if isinstance(expected, float):
        assert actual == pytest.approx(expected)
    else:
        assert actual == expected


def test_tmp_tag_parser_preserves_nested_stacks_breaks_and_invalid_markup() -> None:
    tokens = parse_tmp_text(
        "A<color=#abcdef>B<size=40>C</size>D</color><br>E<unknown=x>F</unknown>",
        _style(),
    )
    runs = [token for token in tokens if isinstance(token, TextRun)]

    assert [(run.text, run.style.color, run.style.size) for run in runs] == [
        ("A", "#112233", 20.0),
        ("B", "#abcdef", 20.0),
        ("C", "#abcdef", 40.0),
        ("D", "#abcdef", 20.0),
        ("E<unknown=x>F</unknown>", "#112233", 20.0),
    ]
    assert sum(isinstance(token, TextBreak) for token in tokens) == 1
    assert [[run.text for run in line] for line in split_runs_by_line(tokens)] == [
        ["A", "B", "C", "D"],
        ["E<unknown=x>F</unknown>"],
    ]


@pytest.mark.parametrize(
    ("kind", "field", "expected"),
    [
        ("alpha", "alpha", 0.25),
        ("size", "size", 18.0),
        ("scale", "scale_x", 1.0),
        ("cspace", "cspace", 0.0),
        ("mspace", "mspace", None),
        ("indent", "indent_percent", 0.2),
        ("line-indent", "line_indent", 0.0),
        ("line-height", "line_height", None),
        ("rotate", "rotate", 0.0),
        ("voffset", "voffset", 0.0),
        ("mark", "mark_color", "#123456"),
        ("b", "bold", True),
        ("i", "italic", True),
        ("u", "underline", True),
        ("s", "strike", True),
        ("pos", "pos_percent", 0.4),
    ],
)
def test_tmp_tag_restoration_uses_previous_style_or_tmp_reset(
    kind: str,
    field: str,
    expected: object,
) -> None:
    previous = replace(
        _style(),
        alpha=0.25,
        size=18.0,
        indent_percent=0.2,
        mark_color="#123456",
        bold=True,
        italic=True,
        underline=True,
        strike=True,
        pos_percent=0.4,
    )
    current = replace(
        _style(),
        alpha=0.9,
        size=42.0,
        scale_x=3.0,
        cspace=8.0,
        mspace=9.0,
        line_indent=7.0,
        line_height=44.0,
        rotate=30.0,
        voffset=5.0,
    )

    restored = restore_tmp_tag_kind(current, previous, kind)

    assert getattr(restored, field) == expected


def _renderer(*, scale_mode: str = "x", anchor_mode: str = "tmp") -> Renderer:
    renderer = Renderer.__new__(Renderer)
    renderer.text_fonts = {1: "Test&Font"}
    renderer.colors = {1: "#112233", 2: "#445566"}
    renderer.tmp_scale_mode = scale_mode
    renderer.text_anchor_mode = anchor_mode
    return renderer


def test_svg_text_renderer_preserves_rich_text_transforms_and_attributes() -> None:
    renderer = _renderer()
    item = {
        "objectData": {
            "visible": True,
            "position": {"x": 0, "y": 0},
            "scale": {"x": 1, "y": 1},
            "rotation": {"z": 0, "w": 1},
        },
        "text": "<scale=2>A</scale><rotate=15><b><i><u><s>B&</s></u></i></b></rotate>\nC",
        "fontId": 1,
        "colorId": 1,
        "outlineColorId": 2,
        "size": 20,
        "outlineSize": 0.1,
        "lineSpacing": 0.25,
        "type": 514,
    }

    svg = renderer.render_text(item)

    assert svg.startswith('<g transform="translate(1024.000 512.000) rotate(-0.00000) scale(1.000000 1.000000)">')
    assert svg.count("<text ") == 3
    assert 'font-family="Test&amp;Font, sans-serif"' in svg
    assert 'stroke="#445566" stroke-width="0.220"' in svg
    assert "scale(2.000000 1)" in svg
    assert "rotate(-15.00000" in svg
    assert 'font-weight="700"' in svg
    assert 'font-style="italic"' in svg
    assert 'text-decoration="underline line-through"' in svg
    assert ">B&amp;</text>" in svg
    assert svg.endswith("</g>")


def test_svg_text_renderer_handles_uniform_centered_and_empty_text() -> None:
    renderer = _renderer(scale_mode="uniform", anchor_mode="center")
    style = replace(_style(), scale_x=2.0)

    assert renderer.svg_text_run_transform(style, 3.0, 4.0) == (40.0, "")
    assert renderer.render_text({"objectData": {"visible": False}, "text": "A"}) == ""
    assert renderer.render_text({"objectData": {"visible": True}, "text": "   "}) == ""
    assert apply_tmp_tag("unknown=x", style) is INVALID_TMP_TAG


@pytest.mark.parametrize(
    ("value", "allow_tmp_invalid", "expected"),
    [
        ("#abc", False, "aabbcc"),
        ("abcd", False, "aabbcc"),
        ("#12345678", False, "123456"),
        ("#g0f", True, "ff00ff"),
        ("", False, None),
        ("12345", False, None),
        ("xyz", False, None),
    ],
)
def test_tmp_color_helpers_accept_supported_forms(
    value: str,
    allow_tmp_invalid: bool,
    expected: str | None,
) -> None:
    assert normalize_color_hex(value, allow_tmp_invalid=allow_tmp_invalid) == expected


def test_tmp_scalar_helpers_cover_units_fallbacks_and_invalid_hex() -> None:
    assert strip_tmp_quotes(" ' value ' ") == "value"
    assert strip_tmp_quotes('"value"') == "value"
    assert [tmp_hex_to_int(value) for value in ("0", "9", "A", "F", "a", "f", "z")] == [0, 9, 10, 15, 10, 15, 15]
    assert tmp_color_alpha("#abc8") == pytest.approx(136 / 255)
    assert tmp_color_alpha("11223340") == pytest.approx(64 / 255)
    assert tmp_color_alpha("#fff") is None
    assert is_tmp_hash_color("#abc")
    assert not is_tmp_hash_color("abc")
    assert not is_tmp_hash_color("#a b")
    assert color_or("#000000", "#g0f") == "#ff00ff"
    assert color_or("#000000", "not-a-color") == "#000000"
    assert parse_hex_alpha("") == 1.0
    assert parse_hex_alpha("8") == pytest.approx(136 / 255)
    assert parse_hex_alpha("80") == pytest.approx(128 / 255)
    assert parse_relaxed_float("12.5px") == 12.5
    with pytest.raises(ValueError, match="could not convert"):
        parse_relaxed_float("px")
    assert parse_float("25%", 3.0) == 25.0
    assert parse_float("bad", 3.0) == 3.0
    assert parse_tmp_numeric("1.5em", 2.0, 20.0) == 30.0
    assert parse_tmp_numeric("12px", 2.0) == 12.0
    assert parse_tmp_numeric("25%", 2.0, 20.0, 80.0) == 20.0
    assert parse_tmp_numeric("25%", 2.0, 20.0) == 5.0
    assert parse_tmp_numeric("bad", 2.0) == 2.0
    assert parse_tmp_percent("25%") == 0.25
    assert parse_tmp_percent("25") is None
    assert parse_tmp_percent("bad%") is None
    assert parse_tmp_scale("3 4", 1.0) == 3.0
    assert parse_tmp_scale("250%", 1.0) == 2.5
    assert parse_tmp_scale("", 1.5) == 1.5
    assert parse_tmp_scale("bad", 1.5) == 1.5
    assert parse_tmp_position("50%", 4.0) == (0.0, 0.5)
    assert parse_tmp_position("12px", 4.0) == (12.0, None)


def test_tmp_tag_kinds_and_ignored_tags_cover_edge_cases() -> None:
    style = _style()

    assert apply_tmp_tag("/color", style) is None
    assert apply_tmp_tag("align=center", style) == style
    assert apply_tmp_tag("nobr", style) == style
    assert tmp_tag_kind("/#abc") == "color"
    assert tmp_tag_kind("<invalid>") is None
    assert restore_tmp_tag_kind(style, replace(style, size=15), "sup").size == 15
    assert restore_tmp_tag_kind(style, replace(style, size=15), "unknown").size == 15

    tokens = parse_tmp_text("A</b>B<", style)
    assert "".join(token.text for token in tokens if isinstance(token, TextRun)) == "AB<"
    assert split_runs_by_line([TextRun("A\n\nB", style)]) == [
        [TextRun("A", style)],
        [],
        [TextRun("B", style)],
    ]


def test_path_png_transform_and_anchor_helpers(tmp_path: Path) -> None:
    quoted = tmp_path / "font's name.otf"
    quoted.write_bytes(b"font")
    uri = file_uri(quoted)

    assert uri == svg_href(quoted)
    assert css_url(quoted).startswith("url('file://")
    assert "%27" in css_url(quoted)
    assert svg_escape('A&B"') == "A&amp;B&quot;"

    png = tmp_path / "sample.png"
    Image.new("RGBA", (7, 5), (1, 2, 3, 4)).save(png)
    invalid = tmp_path / "sample.bin"
    invalid.write_bytes(b"not a png")
    assert png_size(png) == (7, 5)
    assert png_size(invalid) == (1024, 1024)

    assert unity_point({"x": 10, "y": -20}) == (1034.0, 532.0)
    assert unity_rotation_degrees({"z": 0, "w": 0}) == 0.0
    assert unity_rotation_degrees({"z": math.sqrt(0.5), "w": math.sqrt(0.5)}) == pytest.approx(-90.0)
    assert (
        transform_attr(
            {
                "position": {"x": 10, "y": -20},
                "scale": {"x": 2, "y": 3},
                "rotation": {"z": 0, "w": 1},
            },
            0.5,
        )
        == "translate(1034.000 532.000) rotate(-0.00000) scale(1.000000 3.000000)"
    )
    assert [tmp_anchor(value) for value in (0, 2, 4)] == ["start", "middle", "end"]


MASTERDATA_FILES = {
    "customProfileTextColors.json": [{"id": 1, "colorCode": "#112233"}],
    "customProfileTextFonts.json": [{"id": 1, "fontName": "FixtureFont"}],
    "customProfileShapeResources.json": [{"id": 1, "resourceLoadVal": "ignored", "fileName": "shape"}],
    "customProfileGeneralBackgroundResources.json": [],
    "customProfileStoryBackgroundResources.json": [],
    "customProfileCollectionResources.json": [],
    "customProfileEtcResources.json": [],
    "customProfileCharacterIconResources.json": [],
    "customProfileMaterialResources.json": [],
    "customProfileUserInterfaceIconResources.json": [],
}


def _write_masterdata(root: Path) -> Path:
    root.mkdir()
    for name, value in MASTERDATA_FILES.items():
        (root / name).write_text(json.dumps(value), encoding="utf-8")
    return root


def _object(*, layer: int = 0, visible: bool = True) -> dict[str, object]:
    return {
        "visible": visible,
        "layer": layer,
        "position": {"x": 0, "y": 0},
        "scale": {"x": 1, "y": 1},
        "rotation": {"z": 0, "w": 1},
    }


def _fixture_renderer(tmp_path: Path, *, debug: bool = False) -> tuple[Renderer, Path]:
    masterdata = _write_masterdata(tmp_path / "masterdata")
    assets = tmp_path / "assets"
    fonts = tmp_path / "fonts"
    (assets / "shape").mkdir(parents=True)
    fonts.mkdir()
    Image.new("RGBA", (8, 6), "white").save(assets / "shape" / "shape.png")
    (fonts / "FixtureFont.ttf").write_bytes(b"font")
    return Renderer(masterdata, assets, fonts, debug=debug), assets


def test_load_index_and_font_lookup_handle_missing_and_supported_files(tmp_path: Path) -> None:
    assert load_index(tmp_path / "missing.json") == {}
    data = tmp_path / "items.json"
    data.write_text('[{"id": 0}, {"id": "2", "name": "kept"}]', encoding="utf-8")
    assert load_index(data) == {2: {"id": "2", "name": "kept"}}

    fonts = tmp_path / "fonts"
    fonts.mkdir()
    assert font_path(fonts, "Missing") is None
    for suffix in (".otf", ".ttf", "-alt.otf"):
        candidate = fonts / f"Font{suffix}"
        candidate.write_bytes(b"font")
        assert font_path(fonts, "Font") == candidate
        candidate.unlink()


def test_renderer_resolves_resources_fonts_images_shapes_and_masks(tmp_path: Path) -> None:
    renderer, assets = _fixture_renderer(tmp_path)
    nested = assets / "nested"
    nested.mkdir()
    image_path = nested / "image.png"
    Image.new("RGBA", (9, 7), "red").save(image_path)
    root_image = assets / "root.png"
    Image.new("RGBA", (4, 3), "blue").save(root_image)
    other = assets / "other"
    other.mkdir()
    Image.new("RGBA", (2, 2), "green").save(other / "fallback.png")

    assert "@font-face" in renderer.font_css()
    assert renderer.resource_path({}) is None
    assert renderer.resource_path({"resourceLoadVal": "custom_profile/nested", "fileName": "image"}) == image_path
    assert renderer.resource_path({"resourceLoadVal": "custom_profile", "fileName": "root.png"}) == root_image
    assert renderer.resource_path({"resourceLoadVal": "missing", "fileName": "fallback"}, "other") == (
        other / "fallback.png"
    )
    assert renderer.resource_path({"resourceLoadVal": "missing", "fileName": "none"}) is None

    hidden = {"id": 1, "objectData": _object(visible=False)}
    visible = {"id": 1, "objectData": _object()}
    missing = {"id": 99, "objectData": _object()}
    image_svg = renderer.render_image(
        "image",
        visible,
        {"resourceLoadVal": "custom_profile/nested", "fileName": "image"},
    )
    assert renderer.render_image("image", hidden, {}) == ""
    assert 'width="9" height="7"' in image_svg
    assert "image:99" in renderer.render_image("image", missing, {})

    assert renderer.render_shape(hidden) == ""
    assert "shape:99" in renderer.render_shape(missing)
    shape_svg = renderer.render_shape(
        {
            "id": 1,
            "objectData": _object(),
            "colorId": 1,
            "outlineColorId": 1,
            "alpha": 2,
            "outlineAlpha": 0.5,
            "outlineSize": 2,
        }
    )
    assert 'opacity="0.5000"' in shape_svg
    assert 'opacity="1.0000"' in shape_svg
    assert 'mask="url(#mask_1)"' in shape_svg
    assert renderer.defined_masks == 1
    assert len(renderer.defs) == 1

    assert renderer.render_placeholder("shape", hidden) == ""
    assert "shape:1" in renderer.render_placeholder("shape", visible)


def test_renderer_layout_emits_every_category_in_layer_order_and_debug_axes(tmp_path: Path) -> None:
    renderer, _ = _fixture_renderer(tmp_path, debug=True)
    categories = [
        ("generalBackgrounds", "general_background", 9),
        ("storyBackgrounds", "story_background", 8),
        ("collections", "collection", 6),
        ("others", "other", 5),
        ("characterIcons", "character_icon", 4),
        ("materials", "material", 3),
        ("userInterfaceIcons", "user_interface_icon", 2),
    ]
    layout = {name: [{"id": 99, "objectData": _object(layer=layer)}] for name, _label, layer in categories}
    layout["shapes"] = [{"id": 99, "objectData": _object(layer=7)}]
    layout["texts"] = [
        {
            "id": 1,
            "objectData": _object(layer=1),
            "text": "front",
            "fontId": 1,
            "colorId": 1,
            "size": 20,
        }
    ]

    svg = renderer.render_layout({"customProfileCard": layout})

    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    assert svg.count('class="debug-axis"') == 2
    labels = ["front", "user_interface_icon:99", "material:99", "character_icon:99", "other:99"]
    assert [svg.index(label) for label in labels] == sorted(svg.index(label) for label in labels)
    for _name, label, _layer in categories:
        assert f"{label}:99" in svg
    assert "shape:99" in svg


def test_select_cards_and_cli_render_synthetic_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cards = [
        {"seq": 2, "customProfileCardId": 20, "customProfileCard": {}},
        {"seq": 1, "customProfileCardId": 10, "customProfileCard": {}},
    ]
    profile = {"userCustomProfileCards": cards}
    assert select_cards(profile, None, None, True) == [cards[1], cards[0]]
    assert select_cards(profile, None, 20, False) == [cards[0]]
    assert select_cards(profile, 2, None, False) == [cards[0]]
    assert select_cards(profile, None, None, False) == [cards[1]]

    masterdata = _write_masterdata(tmp_path / "masterdata")
    assets = tmp_path / "assets"
    fonts = tmp_path / "fonts"
    output = tmp_path / "output"
    assets.mkdir()
    fonts.mkdir()
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "custom-profile-svg",
            "--profile",
            str(profile_path),
            "--masterdata",
            str(masterdata),
            "--assets",
            str(assets),
            "--fonts",
            str(fonts),
            "--out",
            str(output),
            "--all",
            "--debug",
            "--text-anchor-mode",
            "center",
            "--tmp-scale-mode",
            "uniform",
        ],
    )

    main()

    generated = sorted(output.glob("*.svg"))
    assert [path.name for path in generated] == [
        "custom_profile_seq01_card10.svg",
        "custom_profile_seq02_card20.svg",
    ]
    assert all('class="debug-axis"' in path.read_text(encoding="utf-8") for path in generated)


def test_cli_reports_when_no_card_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text('{"userCustomProfileCards": []}', encoding="utf-8")
    output = tmp_path / "output"
    monkeypatch.setattr(sys, "argv", ["custom-profile-svg", "--profile", str(profile_path), "--out", str(output)])

    with pytest.raises(SystemExit, match="no matching custom profile card"):
        main()
