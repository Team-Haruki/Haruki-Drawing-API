"""Unit tests for the Python Render IR v2 builder (no native extension needed)."""

from __future__ import annotations

import pytest

from src.sekai.base.painter import get_font, get_text_size
import src.sekai.skia_renderer.ir_builder as ir_builder_mod
from src.sekai.skia_renderer.ir_builder import (
    IRBuilder,
    adaptive_color,
    get_pil_font,
    image_shadow,
    image_tint,
    linear_gradient,
    pil_font_cache_info,
    radial_gradient,
)
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT, FONT_DIR


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
    b.triangle_bg([(10.0, 20.0, 45.0, 30.0, 255.0, 0.0, 0.0, 128.0, 1.0)], hour=15.5)
    scene = b.build()
    assert scene["version"] == 2
    assert scene["assets_base_dir"] == "/base"
    assert scene["canvas"] == {"width": 120, "height": 100}
    assert scene["fonts"] == {"dir": "/fonts", "default": "Regular", "bold": "Bold"}
    # The scatter is data, generated once by base/triangle_bg.py and drawn by both backends.
    assert scene["background"] == {
        "type": "TriangleBg",
        "hour": 15.5,
        "tris": [[10.0, 20.0, 45.0, 30.0, 255.0, 0.0, 0.0, 128.0, 1.0]],
    }
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


def test_rect_blend_serializes_src_and_rejects_unknown_values():
    b = _builder()
    b.rect((1, 2), (3, 4), fill=(1, 2, 3, 128), blend="src")

    assert b.build()["root"]["children"][0]["blend"] == "src"
    with pytest.raises(ValueError, match="unsupported Rect blend"):
        b.rect((0, 0), (1, 1), blend="multiply")


def test_image_paste_lerp_serializes_only_for_integral_plain_stretch():
    builder = _builder()
    builder.image(
        "badge.png",
        (2, 3),
        (20, 10),
        fit="stretch",
        sampling="nearest",
        blend="paste_lerp",
    )
    assert builder.build()["root"]["children"][0]["blend"] == "paste_lerp"

    invalid_calls = (
        {"pos": (2.5, 3), "size": (20, 10)},
        {"pos": (2, 3), "size": (0, 10)},
        {"pos": (2, 3), "size": (20, 10), "fit": "contain"},
        {"pos": (2, 3), "size": (20, 10), "alpha": 0.5},
        {"pos": (2, 3), "size": (20, 10), "source_rect": (0, 0, 1, 1)},
        {"pos": (2, 3), "size": (20, 10), "tint": image_tint((255, 0, 0, 255))},
        {"pos": (2, 3), "size": (20, 10), "shadow": image_shadow()},
        {"pos": (2, 3), "size": (20, 10), "blur_sigma": 1.0},
        {"pos": (2, 3), "size": (20, 10), "blur_sigma": (0.0, 1.0)},
        {"pos": (float("inf"), 3), "size": (20, 10)},
    )
    for kwargs in invalid_calls:
        with pytest.raises(ValueError, match="paste_lerp Image"):
            _builder().image("badge.png", blend="paste_lerp", **kwargs)

    with pytest.raises(ValueError, match="unsupported Image blend"):
        _builder().image("badge.png", (0, 0), (1, 1), blend="multiply")


def test_unity_subscene_nests_children_and_placement():
    b = _builder()
    with b.unity_subscene(
        size=(48, 24),
        anchor=(60.5, 40.25),
        object_scale=(1.25, 0.75),
        post_scale=(1.1, 1.2),
        rotation=-12.5,
        sampling="catmull_rom",
        alpha=0.8,
    ):
        b.rect((0, 0), (48, 24), fill=(1, 2, 3, 255))

    node = b.build()["root"]["children"][0]
    assert node == {
        "type": "UnitySubscene",
        "size": [48, 24],
        "anchor": [60.5, 40.25],
        "object_scale": [1.25, 0.75],
        "post_scale": [1.1, 1.2],
        "rotation": -12.5,
        "sampling": "catmull_rom",
        "alpha": 0.8,
        "children": [
            {
                "type": "Rect",
                "pos": [0, 0],
                "size": [48, 24],
                "fill": [1, 2, 3, 255],
            }
        ],
    }


def test_raster_subscene_nests_children_and_validates_placement():
    b = _builder()
    with b.raster_subscene(
        natural_size=(48, 24),
        pos=(10.5, 20.25),
        dst_size=(96, 48),
        sampling="catmull_rom",
        alpha=0.8,
        shadow=image_shadow(0.5, (0, 0), 3.0),
    ):
        b.rect((0, 0), (48, 24), fill=(1, 2, 3, 255))

    node = b.build()["root"]["children"][0]
    assert node["type"] == "RasterSubscene"
    assert node["natural_size"] == [48, 24]
    assert node["pos"] == [10.5, 20.25]
    assert node["dst_size"] == [96.0, 48.0]
    assert node["sampling"] == "catmull_rom"
    assert node["alpha"] == 0.8
    assert node["shadow"]["sigma"] == 3.0
    assert [child["type"] for child in node["children"]] == ["Rect"]

    invalid_calls = (
        {"natural_size": (0, 1), "pos": (0, 0), "dst_size": (1, 1)},
        {"natural_size": (1, 1), "pos": (float("nan"), 0), "dst_size": (1, 1)},
        {"natural_size": (1, 1), "pos": (0, 0), "dst_size": (-1, 1)},
        {"natural_size": (1, 1), "pos": (0, 0), "dst_size": (1, 1), "alpha": 2},
        {
            "natural_size": (1, 1),
            "pos": (0, 0),
            "dst_size": (1, 1),
            "sampling": "pillow_lanczos",
        },
    )
    for kwargs in invalid_calls:
        with pytest.raises(ValueError, match="RasterSubscene"):
            _builder().push_raster_subscene(**kwargs)


def test_pillow_lanczos_sampling_is_serialized_explicitly():
    b = _builder()
    b.image(
        "cards/art.png",
        (-12, -8),
        (144, 116),
        fit="cover",
        sampling="pillow_lanczos",
        blend="src",
    )
    with b.unity_subscene(
        size=(310, 480),
        anchor=(78, 121),
        object_scale=(156 / 310, 242 / 480),
        post_scale=(1, 1),
        rotation=0,
        sampling="pillow_lanczos",
    ):
        b.image("cards/frame.png", (0, 0), (310, 480), sampling="pillow_lanczos")

    image, subscene = b.build()["root"]["children"]
    assert image["sampling"] == "pillow_lanczos"
    assert image["fit"] == "cover"
    assert image["blend"] == "src"
    assert subscene["sampling"] == "pillow_lanczos"
    assert subscene["children"][0]["sampling"] == "pillow_lanczos"


def test_sliced_image_serializes_unity_border_tint_and_alpha():
    b = _builder()
    b.sliced_image(
        path="ui/bg_base_r16_wh.png",
        pos=(4.5, 6.25),
        size=(548, 64),
        border=(21, 21, 21, 21),
        tint=image_tint((244, 246, 252, 230), "recolor"),
        alpha=0.75,
    )

    assert b.build()["root"]["children"][0] == {
        "type": "SlicedImage",
        "path": "ui/bg_base_r16_wh.png",
        "pos": [4.5, 6.25],
        "size": [548, 64],
        "border": [21, 21, 21, 21],
        "tint": {
            "color": [244, 246, 252, 230],
            "mode": "recolor",
            "strength": 1.0,
        },
        "alpha": 0.75,
    }


def test_sdf_font_quad_serializes_registered_font_and_geometry():
    builder = _builder()
    builder.register_extra_font("tmp_dynamic", "/base/fonts/dynamic.ttf")
    builder.sdf_font_quad(
        font_name="tmp_dynamic",
        codepoint=0x25CF,
        sample_size=64.0,
        bbox=(-2, -48, 40, 8),
        padding=6,
        crop_padding=3,
        field_size=(42, 56),
        spread=4.9,
        pos=(3, 4),
        size=(48, 60),
        affine=(1.0, 0.1, -0.25, -0.2, 0.9, 0.5),
        face_color=(250, 240, 230),
        face_scale=1.5,
        face_w=0.35,
        alpha=0.8,
    )

    scene = builder.build()
    assert scene["fonts"]["extra"] == {"tmp_dynamic": "/base/fonts/dynamic.ttf"}
    assert scene["root"]["children"][0] == {
        "type": "SdfFontQuad",
        "font": {"role": "default", "name": "tmp_dynamic", "size": 64.0},
        "codepoint": 0x25CF,
        "bbox": [-2, -48, 40, 8],
        "padding": 6,
        "crop_padding": 3,
        "field_size": [42, 56],
        "spread": 4.9,
        "pos": [3.0, 4.0],
        "size": [48, 60],
        "affine": [1.0, 0.1, -0.25, -0.2, 0.9, 0.5],
        "shading": {
            "face_color": [250, 240, 230],
            "face_scale": 1.5,
            "face_w": 0.35,
            "alpha": 0.8,
            "underlay": None,
        },
    }


def test_background_omitted_when_unset():
    assert "background" not in _builder().build()


def test_cjk_top_is_resolved_with_painter_font_metrics():
    b = IRBuilder(
        120,
        100,
        assets_base_dir=str(ASSETS_BASE_DIR),
        font_dir=str(FONT_DIR),
        default_font=DEFAULT_FONT,
        bold_font=DEFAULT_BOLD_FONT,
    )
    node = b.text("提示Aa", (10, 14), "default", 28, baseline="cjk_top")
    reference_height = get_text_size(get_font(DEFAULT_FONT, 28), "哇")[1]

    assert node["pos"] == [10.0, 14.0 + reference_height]
    assert node["baseline"] == "alphabetic"

    explicit = b.text("提示Aa", (10, 42), "default", 28, baseline="alphabetic")
    assert explicit["pos"] == [10.0, 42.0]
    assert explicit["baseline"] == "alphabetic"


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
        sampling="cubic",
        anchor=(0.5, 1.0),
        tint=image_tint((255, 0, 0, 255), "multiply"),
        shadow=image_shadow(0.5, (3, 3), 2.0),
        blur_sigma=(3.0, 1.5),
    )
    img = b._root_children[-1]
    assert img["fit"] == "crop"
    assert img["sampling"] == "cubic"
    assert img["anchor"] == [0.5, 1.0]
    assert img["tint"]["mode"] == "multiply"
    assert img["shadow"]["sigma"] == 2.0
    assert img["blur_sigma"] == [3.0, 1.5]

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


def test_extra_font_can_be_registered_idempotently_after_builder_creation(tmp_path):
    b = _builder()
    font_path = tmp_path / "font.ttf"
    b.register_extra_font("tmp_dynamic", font_path)
    b.register_extra_font("tmp_dynamic", font_path)

    assert b.build()["fonts"]["extra"] == {"tmp_dynamic": str(font_path)}
    with pytest.raises(ValueError, match="two different paths"):
        b.register_extra_font("tmp_dynamic", tmp_path / "other.ttf")
    with pytest.raises(ValueError, match="non-empty"):
        b.register_extra_font(" ", font_path)


def test_parse_colored_segments():
    from src.sekai.skia_renderer.ir_builder import parse_colored_segments

    segs = parse_colored_segments("a<#ff0000>b<>c", default=(0, 0, 0, 255))
    assert segs == [("a", (0, 0, 0, 255)), ("b", (255, 0, 0, 255)), ("c", (0, 0, 0, 255))]


class _MetricFont:
    def getlength(self, text: str) -> float:
        return float(len(text))

    def getbbox(self, text: str) -> tuple[int, int, int, int]:
        return (0, -2, len(text), 3)


def test_font_cache_reprobes_fallback_and_evicts_oldest(monkeypatch):
    cache = ir_builder_mod._thread_font_cache()
    cache.clear()
    loaded: list[tuple[str, str, int]] = []

    def fake_load(font_dir: str, name: str, px: int):
        loaded.append((font_dir, name, px))
        return _MetricFont(), name != "missing"

    monkeypatch.setattr(ir_builder_mod, "_load_pil_font", fake_load)
    monkeypatch.setattr(ir_builder_mod, "_PIL_FONT_CACHE_MAX", 2)

    assert get_pil_font("/fonts", "missing", 0.1) is not get_pil_font("/fonts", "missing", 0.1)
    first = get_pil_font("/fonts", "first", 12.2)
    assert get_pil_font("/fonts", "first", 12.4) is first
    get_pil_font("/fonts", "second", 13)
    get_pil_font("/fonts", "third", 14)

    assert loaded.count(("/fonts", "missing", 1)) == 2
    assert loaded.count(("/fonts", "first", 12)) == 1
    assert pil_font_cache_info() == {"size": 2, "max": 2}
    assert ("/fonts", "first", 12) not in cache
    cache.clear()


def test_load_pil_font_skips_broken_face_and_falls_back(monkeypatch):
    from PIL import ImageFont

    attempted: list[str] = []
    sentinel = object()
    monkeypatch.setattr(ir_builder_mod.os.path, "exists", lambda path: path.endswith((".otf", ".ttf")))

    def fake_truetype(path: str, px: int):
        attempted.append(path)
        if path.endswith(".otf"):
            raise OSError("broken face")
        return sentinel

    monkeypatch.setattr(ImageFont, "truetype", fake_truetype)
    assert ir_builder_mod._load_pil_font("/fonts", "Face", 18) == (sentinel, True)
    assert attempted == ["/fonts/Face.otf", "/fonts/Face.ttf"]

    monkeypatch.setattr(ir_builder_mod.os.path, "exists", lambda _path: False)
    monkeypatch.setattr(ImageFont, "load_default", lambda: sentinel)
    fallback, resolved = ir_builder_mod._load_pil_font("/fonts", "Missing", 18)
    assert fallback is sentinel
    assert resolved is False


def test_rich_text_layout_emits_wrapped_colored_and_shadowed_nodes(monkeypatch):
    builder = _builder()
    font = _MetricFont()
    monkeypatch.setattr(builder, "_pil_font", lambda *_args, **_kwargs: font)

    assert builder.measure_text("abc", "default", 12) == 3.0
    assert builder.measure_text_ink("abc", "default", 12) == (3.0, 5.0)
    assert builder.painter_baseline_y(7, "default", 12) == 12.0
    assert builder.wrap_text("ab cd\nef", "default", 12, 3) == ["ab", "cd", "ef"]
    assert builder.wrap_text("ab  cd", "default", 12, 2) == ["ab", "", "cd"]

    multiline = builder.multiline_text(
        "abcd efgh ijkl",
        (10, 20),
        "default",
        10,
        max_width=4,
        max_lines=2,
        line_height=11,
        ellipsis=".",
        baseline="alphabetic",
    )
    assert [node["text"] for node in multiline] == ["abcd", "efg."]
    assert [node["pos"] for node in multiline] == [[10.0, 20.0], [10.0, 31.0]]

    centered = builder.colored_text(
        [("ab", None), ("c", (1, 2, 3, 255))],
        (10, 40),
        "default",
        12,
        align="center",
        baseline="alphabetic",
        default_fill=(9, 8, 7, 255),
    )
    assert [node["pos"][0] for node in centered] == [8.5, 10.5]
    assert centered[0]["fill"] == [9, 8, 7, 255]
    assert centered[1]["fill"] == [1, 2, 3, 255]

    right = builder.colored_text(
        [("a", None), ("bc", None)],
        (10, 50),
        "default",
        12,
        align="right",
        baseline="alphabetic",
    )
    assert [node["pos"][0] for node in right] == [7.0, 8.0]

    shadow, top = builder.shadowed_text(
        "x",
        (3, 4),
        "bold",
        14,
        shadow_offset=(2, 3),
        baseline="alphabetic",
    )
    assert shadow["pos"] == [5.0, 7.0]
    assert top["pos"] == [3.0, 4.0]


def test_extended_shapes_gradients_backgrounds_and_watermark():
    builder = _builder()
    gradient = radial_gradient(
        stops=[((255, 0, 0, 255), 0.0), ((0, 0, 255, 128), 1.0)],
        center=(4, 5),
        radius_px=9,
    )
    builder.pieslice(
        (1, 2),
        (30, 40),
        20,
        250,
        fill=gradient,
        stroke=(2, 3, 4, 255),
        stroke_width=3,
    )
    builder.watermark([("line", (4, 6), "center")], "default", 12, font_name="serif")
    builder.blurglass(
        (2, 4),
        (40, 20),
        6,
        (10, 20, 30, 128),
        blur=7,
        shadow_width=9,
        corners=(True, False, True, False),
    )
    builder.triangle_bg([], hour=8, time_color=False, main_hue=123.5)

    scene = builder.build()
    pie, watermark, glass = scene["root"]["children"]
    assert pie["fill"] == gradient
    assert pie["stroke_width"] == 3
    assert watermark["font"]["name"] == "serif"
    assert glass["blur"] == 7.0
    assert glass["shadow_width"] == 9.0
    assert glass["corners"] == [True, False, True, False]
    assert scene["background"] == {
        "type": "TriangleBg",
        "hour": 8,
        "time_color": False,
        "main_hue": 123.5,
        "tris": [],
    }
    assert adaptive_color(pixelwise=True)["pixelwise"] is True

    builder.image_bg("bg.png", mode="cover", align="rb", blur=True, fade=0.25)
    assert builder.build()["background"] == {
        "type": "ImageBg",
        "path": "bg.png",
        "mode": "cover",
        "align": "rb",
        "blur": True,
        "fade": 0.25,
    }
