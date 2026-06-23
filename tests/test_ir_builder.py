"""Unit tests for the Python Render IR v2 builder (no native extension needed)."""

from __future__ import annotations

from src.sekai.skia_renderer.ir_builder import IRBuilder, linear_gradient


def _builder() -> IRBuilder:
    return IRBuilder(
        120,
        100,
        assets_base_dir="/base",
        font_dir="/fonts",
        default_font="Regular",
        bold_font="Bold",
        export_format="png",
        jpg_quality=90,
    )


def test_build_envelope_and_root():
    b = _builder()
    b.triangle_bg(15.5)
    scene = b.build()
    assert scene["version"] == 2
    assert scene["assets_base_dir"] == "/base"
    assert scene["canvas"] == {"width": 120, "height": 100}
    assert scene["fonts"] == {"dir": "/fonts", "default": "Regular", "bold": "Bold"}
    assert scene["background"] == {"type": "TriangleBg", "hour": 15.5}
    assert scene["root"]["type"] == "Group"
    assert scene["root"]["size"] == [120, 100]


def test_group_nesting_and_node_shapes():
    b = _builder()
    b.rect((1, 2), (3, 4), fill=(255, 0, 0, 255), stroke=(0, 0, 0, 255), stroke_width=2)
    with b.group((10, 20), (30, 40)):
        b.roundrect((0, 0), (10, 10), 4, fill=linear_gradient((0, 0, 0, 255), (255, 255, 255, 255), (0, 0), (10, 10)))
        b.text("hi", (1, 1), "bold", 12, align="center", baseline="alphabetic", fill=(1, 2, 3, 255))

    children = b.build()["root"]["children"]
    assert [c["type"] for c in children] == ["Rect", "Group"]

    rect = children[0]
    assert rect["pos"] == [1, 2]
    assert rect["size"] == [3, 4]
    assert rect["fill"] == [255, 0, 0, 255]
    assert rect["stroke"] == [0, 0, 0, 255]
    assert rect["stroke_width"] == 2

    group = children[1]
    assert group["offset"] == [10, 20]
    inner = group["children"]
    assert [c["type"] for c in inner] == ["RoundRect", "Text"]
    assert inner[0]["fill"]["kind"] == "linear"
    assert inner[1]["font"] == {"role": "bold", "size": 12}
    assert inner[1]["align"] == "center"
    assert inner[1]["baseline"] == "alphabetic"


def test_background_omitted_when_unset():
    assert "background" not in _builder().build()
