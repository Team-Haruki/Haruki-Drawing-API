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


def test_gradient_radial_and_stroke_and_corner_radii():
    from src.sekai.skia_renderer.ir_builder import radial_gradient

    b = _builder()
    grad = linear_gradient(stops=[((255, 0, 0, 255), 0.0), ((0, 0, 255, 255), 1.0)], p1=(0, 0), p2=(10, 0))
    b.roundrect(
        (0, 0),
        (10, 10),
        4,
        fill=grad,
        stroke=radial_gradient((0, 0, 0, 255), (255, 255, 255, 255), center=(5, 5), radius_px=5),
        stroke_width=2,
        corner_radii=(8, 0, 8, 0),
    )
    node = b._root_children[-1]
    assert node["fill"]["kind"] == "linear"
    assert len(node["fill"]["stops"]) == 2
    assert node["stroke"]["kind"] == "radial"
    assert node["corner_radii"] == [8.0, 0.0, 8.0, 0.0]


def test_image_tint_shadow_and_text_extras():
    from src.sekai.skia_renderer.ir_builder import adaptive_color, image_shadow, image_tint, text_stroke

    b = _builder()
    b.image(
        "a.png",
        (0, 0),
        (10, 10),
        fit="crop",
        tint=image_tint((255, 0, 0, 255), "multiply"),
        shadow=image_shadow(0.5, (3, 3), 2.0),
    )
    img = b._root_children[-1]
    assert img["fit"] == "crop"
    assert img["tint"]["mode"] == "multiply"
    assert img["shadow"]["sigma"] == 2.0

    b.text(
        "hi",
        (0, 0),
        "bold",
        20,
        fill=linear_gradient((255, 0, 0, 255), (0, 0, 255, 255), (0, 0), (10, 0)),
        stroke=text_stroke((0, 0, 0, 255), 2),
        letter_spacing=1.5,
        adaptive=adaptive_color(),
        font_name="serif",
    )
    txt = b._root_children[-1]
    assert txt["fill"]["kind"] == "linear"
    assert txt["stroke"]["width"] == 2.0
    assert txt["letter_spacing"] == 1.5
    assert txt["adaptive"]["threshold"] == 0.4
    assert txt["font"]["name"] == "serif"


def test_extra_fonts_and_watermark():
    b = IRBuilder(
        100,
        100,
        assets_base_dir="/base",
        font_dir="/fonts",
        default_font="Regular",
        bold_font="Bold",
        extra_fonts={"serif": "MySerif"},
    )
    assert b.build()["fonts"]["extra"] == {"serif": "MySerif"}
    b.watermark([("hello", (5, 5), "left"), ("world", (95, 5), "right")], "default", 16)
    wm = b._root_children[-1]
    assert wm["type"] == "Watermark"
    assert len(wm["lines"]) == 2
    assert wm["lines"][1]["align"] == "right"


def test_parse_colored_segments():
    from src.sekai.skia_renderer.ir_builder import parse_colored_segments

    segs = parse_colored_segments("a<#ff0000>b<>c", default=(0, 0, 0, 255))
    assert segs == [("a", (0, 0, 0, 255)), ("b", (255, 0, 0, 255)), ("c", (0, 0, 0, 255))]
