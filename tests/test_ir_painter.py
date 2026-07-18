"""IRPainter shim: render a plot.py Canvas tree to Skia and check it matches the layout."""

from __future__ import annotations

import asyncio
from io import BytesIO
import json

import numpy as np
from PIL import Image
import pytest

try:
    import haruki_skia_renderer as _native
except ImportError:  # pragma: no cover - extension not built
    _native = None

from src.sekai.base.draw import Canvas, TextBox, roundrect_bg
from src.sekai.base.painter import ImageTint
from src.sekai.base.plot import TextStyle, VSplit
from src.sekai.base.utils import get_img_from_path
from src.sekai.skia_renderer.ir_painter import IRPainter
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT, FONT_DIR

DEF = DEFAULT_FONT

pytestmark = pytest.mark.skipif(_native is None, reason="haruki_skia_renderer not built")


def _build_canvas() -> Canvas:
    with Canvas().set_padding(10) as canvas:
        with VSplit().set_sep(6).set_bg(roundrect_bg(alpha=80)).set_padding(8):
            TextBox("行一 Hello", style=TextStyle(font=DEFAULT_FONT, size=20, color=(0, 0, 0, 255)))
            TextBox("行二 World", style=TextStyle(font=DEFAULT_FONT, size=20, color=(20, 40, 60, 255)))
    return canvas


def _render(canvas: Canvas):
    size = canvas._get_self_size()
    p = IRPainter(
        size,
        assets_base_dir=str(ASSETS_BASE_DIR),
        font_dir=str(FONT_DIR),
        default_font=DEF,
        bold_font=DEFAULT_BOLD_FONT,
        bg_hour=15.5,
    )
    canvas.draw(p)
    scene, mem = p.build_scene()
    result = _native.render_scene(json.dumps(scene, ensure_ascii=False).encode(), mem)
    return size, result


def test_irpainter_renders_canvas_matching_layout():
    canvas = _build_canvas()
    size, result = _render(canvas)
    assert (result["image_width"], result["image_height"]) == tuple(size)
    img = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")
    assert img.size == tuple(size)
    # The frosted panel + text means the canvas is not blank.
    assert img.getbbox() is not None


def test_irpainter_mem_image_renders():
    red = Image.new("RGBA", (24, 24), (255, 0, 0, 255))
    p = IRPainter(
        (40, 40),
        assets_base_dir=str(ASSETS_BASE_DIR),
        font_dir=str(FONT_DIR),
        default_font=DEF,
        bold_font=DEFAULT_BOLD_FONT,
    )
    p.paste(red, (8, 8), (24, 24))
    scene, mem = p.build_scene()
    assert len(mem) == 1  # the runtime image was captured as a mem:<key> entry
    result = _native.render_scene(json.dumps(scene).encode(), mem)
    img = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")
    assert img.getpixel((20, 20))[0] > 200  # red present


def test_irpainter_path_image_renders_without_mem_transport(tmp_path):
    asset = tmp_path / "icons" / "red.png"
    asset.parent.mkdir(parents=True)
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(asset)
    source = asyncio.run(get_img_from_path(tmp_path, "icons/red.png", on_missing="raise"))

    painter = IRPainter(
        (40, 40),
        assets_base_dir=str(tmp_path),
        font_dir=str(FONT_DIR),
        default_font=DEF,
        bold_font=DEFAULT_BOLD_FONT,
    )
    painter.paste(source, (8, 8), (24, 24))
    scene, mem = painter.build_scene()
    assert mem == {}
    assert scene["root"]["children"][0]["path"] == "icons/red.png"

    result = _native.render_scene(json.dumps(scene).encode(), mem)
    img = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")
    assert img.getpixel((20, 20))[0] > 200
    source.close()


def test_irpainter_gradient_text_maps_to_glyph_overlay_fill():
    # Gradient text no longer raises SkiaUnsupported: the gradient endpoints are mapped
    # onto the glyph overlay (ink bbox + 10px) and the Rust Text node renders the
    # gradient as a glyph-masked shader.
    from src.sekai.base.painter import FontDesc, LinearGradient

    p = IRPainter(
        (40, 40),
        assets_base_dir=str(ASSETS_BASE_DIR),
        font_dir=str(FONT_DIR),
        default_font=DEF,
        bold_font=DEFAULT_BOLD_FONT,
    )
    grad = LinearGradient((255, 0, 0, 255), (0, 0, 255, 255), (0, 0), (1, 0))
    p.text("hi", (0, 0), FontDesc(DEF, 20), fill=grad)
    scene, _mem = p.build_scene()
    texts = [n for n in scene["root"]["children"] if n.get("type") == "Text"]
    assert texts, "expected a text node in the scene"
    fill = texts[0]["fill"]
    assert isinstance(fill, dict)
    assert fill.get("kind") == "linear"


def _painter(size=(60, 60), assets_base_dir=None) -> IRPainter:
    return IRPainter(
        size,
        assets_base_dir=str(assets_base_dir or ASSETS_BASE_DIR),
        font_dir=str(FONT_DIR),
        default_font=DEF,
        bold_font=DEFAULT_BOLD_FONT,
    )


def test_irpainter_encoded_image_ref_ships_bytes_untouched():
    from src.sekai.base.utils import get_encoded_image_ref

    buf = BytesIO()
    Image.new("RGBA", (16, 16), (0, 255, 0, 255)).save(buf, "PNG")
    ref = get_encoded_image_ref(buf.getvalue())

    p = _painter((40, 40))
    p.paste(ref, (8, 8), (24, 24))
    p.paste(ref, (0, 0), (8, 8))  # same ref → same mem entry
    scene, mem = p.build_scene()
    assert mem == {"m0": ref.data}  # raw encoded bytes, no Python decode/re-encode
    result = _native.render_scene(json.dumps(scene).encode(), mem)
    img = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")
    assert img.getpixel((20, 20))[1] > 200  # green present


def test_irpainter_asset_image_ref_emits_relative_path(tmp_path):
    from src.sekai.base.utils import get_asset_image_ref

    asset = tmp_path / "icons" / "blue.png"
    asset.parent.mkdir(parents=True)
    Image.new("RGBA", (8, 8), (0, 0, 255, 255)).save(asset)
    ref = asyncio.run(get_asset_image_ref(tmp_path, "icons/blue.png"))

    p = _painter((40, 40), assets_base_dir=tmp_path)
    p.paste(ref, (8, 8), (24, 24))
    scene, mem = p.build_scene()
    assert mem == {}
    assert scene["root"]["children"][0]["path"] == "icons/blue.png"


def test_irpainter_imagebg_native_parity_and_raster_cache_isolation(tmp_path):
    """A decorated background stays close to the Pillow oracle and out of the shared cache.

    Render filtered -> unfiltered -> filtered with the same path/target. Exact repeatability
    proves the Moka target raster contains only the resize, not a previous node's tint/blur.
    The non-zero region catches filter halos leaking outside the destination rectangle.
    """
    from src.sekai.base.painter import Painter
    from src.sekai.base.plot import ImageBg
    from src.sekai.base.utils import get_asset_image_ref

    width, height = 24, 18
    source = Image.new("RGBA", (width, height))
    source.putdata(
        [
            ((x * 47 + y * 13) % 256, (x * 19 + y * 61) % 256, (x * 83 + y * 7) % 256, 255)
            for y in range(height)
            for x in range(width)
        ]
    )
    asset = tmp_path / "bg.png"
    source.save(asset)
    ref = asyncio.run(get_asset_image_ref(tmp_path, "bg.png", on_missing="raise"))
    canvas_size = (80, 60)
    region_pos = (13, 11)
    region_size = (54, 36)
    base_color = (17, 23, 31, 255)

    def add_tree(painter, *, blur, fade):
        painter.rect((0, 0), canvas_size, base_color)
        painter.set_region(region_pos, region_size)
        ImageBg(ref, mode="fill", blur=blur, fade=fade).draw(painter)
        painter.restore_region()

    def render_native(*, blur, fade):
        painter = _painter(canvas_size, assets_base_dir=tmp_path)
        add_tree(painter, blur=blur, fade=fade)
        scene, mem = painter.build_scene()
        assert mem == {}
        result = _native.render_scene(json.dumps(scene).encode(), mem)
        return Image.open(BytesIO(result["image_bytes"])).convert("RGBA")

    pillow = Painter(size=canvas_size)
    add_tree(pillow, blur=True, fade=0.1)
    expected = Painter._execute(pillow.operations, None, canvas_size).convert("RGBA")

    cold_filtered = render_native(blur=True, fade=0.1)
    unfiltered = render_native(blur=False, fade=0)
    warm_filtered = render_native(blur=True, fade=0.1)
    unfiltered_again = render_native(blur=False, fade=0)

    expected_px = np.asarray(expected).astype(np.int16)
    actual_px = np.asarray(cold_filtered).astype(np.int16)
    rgb_delta = np.abs(expected_px[:, :, :3] - actual_px[:, :, :3])
    assert float(rgb_delta.mean()) <= 3.0
    assert float(np.quantile(rgb_delta, 0.99)) <= 16.0
    assert int(rgb_delta.max()) <= 32
    assert np.array_equal(expected_px[:, :, 3], actual_px[:, :, 3])
    assert cold_filtered.getpixel((4, 4)) == base_color  # blur did not escape the nested region

    assert np.array_equal(np.asarray(cold_filtered), np.asarray(warm_filtered))
    assert np.array_equal(np.asarray(unfiltered), np.asarray(unfiltered_again))
    assert not np.array_equal(np.asarray(cold_filtered), np.asarray(unfiltered))


def test_irpainter_crop_sampling_and_tint_raster_cache_isolation(tmp_path):
    """The target-raster cache key owns geometry/sampling, while tint stays draw-local.

    Exercise one path, destination size and source rect in both orders. A stale target raster
    or a tint baked into the cache makes at least one reverse pass differ from its cold render.
    """
    from src.sekai.base.utils import get_asset_image_ref

    width, height = 23, 17
    source = Image.new("RGBA", (width, height))
    source.putdata(
        [
            (
                (x * 47 + y * 13) % 256,
                (x * 19 + y * 61) % 256,
                (x * 83 + y * 7) % 256,
                (x * 31 + y * 43 + 17) % 256,
            )
            for y in range(height)
            for x in range(width)
        ]
    )
    asset = tmp_path / "map.png"
    source.save(asset)
    ref = asyncio.run(get_asset_image_ref(tmp_path, "map.png", on_missing="raise"))
    canvas_size = (15, 11)
    source_rect = (2, 3, 20, 15)
    base_color = (11, 29, 47, 255)
    multiply = ImageTint((103, 207, 71, 255), mode="multiply")
    recolor = ImageTint((255, 32, 32, 255), mode="recolor")
    variants = [
        ("plain-linear", None, "linear"),
        ("multiply-linear", multiply, "linear"),
        ("recolor-linear", recolor, "linear"),
        ("plain-catmull", None, "catmull_rom"),
        ("multiply-catmull", multiply, "catmull_rom"),
        ("recolor-catmull", recolor, "catmull_rom"),
    ]

    def render(tint, sampling):
        painter = _painter(canvas_size, assets_base_dir=tmp_path)
        painter.rect((0, 0), canvas_size, fill=base_color)
        painter.paste_with_alpha_blend(
            ref,
            (0, 0),
            canvas_size,
            src_rect=source_rect,
            sampling=sampling,
            tint=tint,
        )
        scene, mem = painter.build_scene()
        node = scene["root"]["children"][1]
        assert mem == {}
        assert node["path"] == "map.png"
        assert node["size"] == list(canvas_size)
        assert node["source_rect"] == [float(v) for v in source_rect]
        assert node["sampling"] == sampling
        result = _native.render_scene(json.dumps(scene).encode(), mem)
        return np.asarray(Image.open(BytesIO(result["image_bytes"])).convert("RGBA")).copy()

    cold = {name: render(tint, sampling) for name, tint, sampling in variants}
    for name, tint, sampling in reversed(variants):
        assert np.array_equal(render(tint, sampling), cold[name]), name

    for sampling in ("linear", "catmull"):
        assert not np.array_equal(cold[f"plain-{sampling}"], cold[f"multiply-{sampling}"])
        assert not np.array_equal(cold[f"multiply-{sampling}"], cold[f"recolor-{sampling}"])
    for tint_name in ("plain", "multiply", "recolor"):
        assert not np.array_equal(cold[f"{tint_name}-linear"], cold[f"{tint_name}-catmull"])


def test_irpainter_multiply_tint_scales_implicit_rgb_alpha(tmp_path):
    """An RGB asset's implicit alpha is modulated by tint alpha on both backends."""
    from src.sekai.base.utils import get_asset_image_ref

    asset = tmp_path / "rgb.png"
    Image.new("RGB", (2, 1), (120, 80, 40)).save(asset)
    ref = asyncio.run(get_asset_image_ref(tmp_path, "rgb.png", on_missing="raise"))
    painter = _painter((2, 1), assets_base_dir=tmp_path)
    painter.paste_src(ref, (0, 0), tint=ImageTint((128, 64, 255, 96), mode="multiply"))
    scene, mem = painter.build_scene()

    result = _native.render_scene(json.dumps(scene).encode(), mem)
    image = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")

    assert list(image.getchannel("A").get_flattened_data()) == [96, 96]


def test_irpainter_paste_shadow_uses_pre_tint_silhouette(tmp_path):
    """Rust builds the drop shadow from the untinted silhouette; tinting must only
    change the pasted interior, never the shadow ring (mirrors the Pillow-side test)."""
    from src.sekai.base.utils import get_asset_image_ref

    asset = tmp_path / "badge.png"
    Image.new("RGBA", (10, 8), (200, 40, 40, 255)).save(asset)
    ref = asyncio.run(get_asset_image_ref(tmp_path, "badge.png", on_missing="raise"))
    interior = (10, 9, 20, 17)  # pos + source size

    def render(tint):
        painter = _painter((30, 26), assets_base_dir=tmp_path)
        painter.rect((0, 0), (30, 26), fill=(255, 255, 255, 255))
        painter.paste_with_alpha_blend(ref, (10, 9), use_shadow=True, shadow_width=6, tint=tint)
        scene, mem = painter.build_scene()
        result = _native.render_scene(json.dumps(scene).encode(), mem)
        return Image.open(BytesIO(result["image_bytes"])).convert("RGBA")

    untinted = render(None)
    tinted = render(ImageTint((32, 32, 255, 128), mode="recolor"))

    assert not np.array_equal(np.asarray(untinted.crop(interior)), np.asarray(tinted.crop(interior)))  # the tint drew
    cover = Image.new("RGBA", (10, 8), (0, 0, 0, 255))
    masked_untinted, masked_tinted = untinted.copy(), tinted.copy()
    masked_untinted.paste(cover, interior[:2])
    masked_tinted.paste(cover, interior[:2])
    assert np.array_equal(np.asarray(masked_untinted), np.asarray(masked_tinted))


def test_irpainter_paste_src_nearest_matches_pillow_kernel(tmp_path):
    """``sampling="nearest"`` on paste_src under a real 4x upscale is exact on the
    native backend and identical to PIL NEAREST (no other test exercises nearest)."""
    from src.sekai.base.utils import get_asset_image_ref

    source = Image.new("RGBA", (2, 2))
    source.putdata(
        [
            (255, 255, 255, 255),
            (0, 0, 0, 255),
            (0, 0, 0, 255),
            (255, 255, 255, 255),
        ]
    )
    asset = tmp_path / "checker.png"
    source.save(asset)
    ref = asyncio.run(get_asset_image_ref(tmp_path, "checker.png", on_missing="raise"))
    painter = _painter((8, 8), assets_base_dir=tmp_path)
    painter.paste_src(ref, (0, 0), (8, 8), sampling="nearest")
    scene, mem = painter.build_scene()

    result = _native.render_scene(json.dumps(scene).encode(), mem)
    image = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")

    expected = source.resize((8, 8), Image.Resampling.NEAREST)
    assert np.array_equal(np.asarray(image), np.asarray(expected))


def test_irpainter_clip_roundrect_group_relative_children():
    p = _painter((60, 60))
    p.push_clip_roundrect((10, 10), (40, 40), 12)
    p.rect((10, 10), (40, 40), fill=(255, 0, 0, 255))
    p.pop_clip()
    p.rect((0, 0), (5, 5), fill=(0, 0, 0, 255))  # after pop: absolute again
    scene, mem = p.build_scene()

    group, tail = scene["root"]["children"]
    assert group["type"] == "Group"
    assert group["offset"] == [10.0, 10.0]
    assert group["size"] == [40.0, 40.0]
    assert group["clip"] == {"kind": "rrect", "radius": 12.0, "corners": [True, True, True, True]}
    assert group["children"][0]["pos"] == [0.0, 0.0]  # group-relative
    assert tail["pos"] == [0.0, 0.0]

    result = _native.render_scene(json.dumps(scene).encode(), mem)
    img = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")
    assert img.getpixel((30, 30))[3] == 255  # kept inside clip
    assert img.getpixel((11, 11))[3] < 128  # rounded corner clipped away


def test_irpainter_unbalanced_clip_raises_skia_unsupported():
    from src.sekai.skia_renderer.ir_painter import SkiaUnsupported

    p = _painter()
    p.push_clip_roundrect((0, 0), (10, 10), 2)
    with pytest.raises(SkiaUnsupported):
        p.build_scene()
    with pytest.raises(SkiaUnsupported):
        _painter().pop_clip()


def test_irpainter_push_mask_emits_masked_group_and_multiplies_alpha():
    """Painter.push_mask -> Group{mask} (saveLayer + DstIn), i.e. the same alpha multiply the
    Pillow backend applies in _impl_pop_mask (see test_image_source)."""
    mask = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    mask.paste(Image.new("RGBA", (20, 40), (255, 255, 255, 255)), (0, 0))  # left half opaque

    p = _painter((40, 40))
    p.push_mask(mask, (0, 0), (40, 40))
    p.rect((0, 0), (40, 40), fill=(255, 0, 0, 255))
    p.pop_mask()
    scene, mem = p.build_scene()

    group = scene["root"]["children"][0]
    assert group["type"] == "Group"
    assert group["offset"] == [0.0, 0.0]
    assert group["mask"].startswith("mem:")  # runtime image -> mem ref, and it must travel
    assert group["mask"][4:] in mem
    assert group["children"][0]["pos"] == [0.0, 0.0]  # group-relative

    img = Image.open(BytesIO(_native.render_scene(json.dumps(scene).encode(), mem)["image_bytes"])).convert("RGBA")
    assert img.getpixel((10, 20)) == (255, 0, 0, 255)  # kept where the mask is opaque
    assert img.getpixel((30, 20))[3] == 0  # masked away


def test_irpainter_pop_mask_cannot_close_a_clip_group():
    from src.sekai.skia_renderer.ir_painter import SkiaUnsupported

    p = _painter()
    p.push_clip_roundrect((0, 0), (10, 10), 2)
    with pytest.raises(SkiaUnsupported):
        p.pop_mask()


def test_irpainter_shadow_roundrect_emits_shadow_node():
    p = _painter()
    p.shadow_roundrect((10, 10), (30, 30), 8, shadow_width=6, shadow_alpha=0.4)
    scene, _mem = p.build_scene()
    node = scene["root"]["children"][0]
    assert node["type"] == "Shadow"
    assert node["pos"] == [10.0, 10.0]
    assert node["radius"] == 8
    assert node["alpha"] == 0.4


def test_irpainter_paste_src_rect_emits_source_rect_and_renders(tmp_path):
    from src.sekai.base.utils import get_asset_image_ref

    asset = tmp_path / "icons" / "half.png"
    asset.parent.mkdir(parents=True)
    src = Image.new("RGBA", (20, 10), (255, 0, 0, 255))
    src.paste((0, 255, 0, 255), (10, 0, 20, 10))  # right half green
    src.save(asset)
    ref = asyncio.run(get_asset_image_ref(tmp_path, "icons/half.png"))

    p = _painter((16, 16), assets_base_dir=tmp_path)
    p.paste(ref, (0, 0), (16, 16), src_rect=(10, 0, 20, 10))
    scene, mem = p.build_scene()
    node = scene["root"]["children"][0]
    assert mem == {}  # still pure path transport
    assert node["path"] == "icons/half.png"
    assert node["source_rect"] == [10.0, 0.0, 20.0, 10.0]

    result = _native.render_scene(json.dumps(scene).encode(), mem)
    img = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")
    px = img.getpixel((8, 8))
    assert px[1] > 200  # green slice present
    assert px[0] < 60  # red half cropped away


def test_native_self_image_samples_rendered_content():
    from src.sekai.skia_renderer.ir_builder import IRBuilder
    from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, FONT_DIR

    b = IRBuilder(
        20,
        30,
        assets_base_dir=str(ASSETS_BASE_DIR),
        font_dir=str(FONT_DIR),
        default_font=DEF,
        bold_font=DEFAULT_BOLD_FONT,
    )
    # Top 20x20: left half red, right half blue. Footer 20x10 stretches the bottom strip.
    b.rect((0, 0), (10, 20), fill=(255, 0, 0, 255))
    b.rect((10, 0), (10, 20), fill=(0, 0, 255, 255))
    b.self_image((0, 20), (20, 10), source_rect=(0, 10, 20, 20))
    result = _native.render_scene(json.dumps(b.build()).encode(), {})
    img = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")
    assert img.getpixel((4, 25))[0] > 200  # footer left = red sample
    assert img.getpixel((15, 25))[2] > 200  # footer right = blue sample
