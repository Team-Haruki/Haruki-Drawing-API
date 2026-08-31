from dataclasses import replace

import pytest

from src.sekai.profile.custom_profile.svg import (
    INVALID_TMP_TAG,
    Renderer,
    TextBreak,
    TextRun,
    TextStyle,
    apply_tmp_tag,
    parse_tmp_text,
    restore_tmp_tag_kind,
    split_runs_by_line,
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
