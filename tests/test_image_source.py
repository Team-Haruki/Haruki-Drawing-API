"""Unified ImageSource: lazy refs through layout, Pillow decode-on-demand, clip/shadow ops."""

from __future__ import annotations

import asyncio
from io import BytesIO
import random

from PIL import Image, ImageChops, ImageEnhance, ImageFilter
import pytest

from src.sekai.base import plot, utils
from src.sekai.base.img_utils import multiply_image_by_color
from src.sekai.base.painter import ImageTint, Painter
from src.sekai.base.plot import Canvas, FillBg, HSplit, ImageBox
from src.sekai.base.utils import (
    AssetImageRef,
    EncodedImageRef,
    get_asset_image_ref,
    get_encoded_image_ref,
    get_img_from_path,
    resolve_image_source_sync,
)


def _png_bytes(size=(40, 30), color=(255, 0, 0, 255)) -> bytes:
    buf = BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_encoded_image_ref_probe_and_resolve():
    ref = get_encoded_image_ref(_png_bytes())
    assert isinstance(ref, EncodedImageRef)
    assert ref.size == (40, 30)
    assert ref.width == 40
    assert ref.height == 30
    img = resolve_image_source_sync(ref)
    assert img.size == (40, 30)
    assert img.getpixel((1, 1)) == (255, 0, 0, 255)


def test_asset_image_ref_resolve_and_missing_placeholder(tmp_path):
    path = tmp_path / "a.png"
    Image.new("RGBA", (8, 6), (0, 255, 0, 255)).save(path)
    stat = path.stat()
    ref = AssetImageRef(path=path, size=(8, 6), mode="RGBA", mtime_ns=stat.st_mtime_ns, file_size=stat.st_size)
    img = resolve_image_source_sync(ref)
    assert img.size == (8, 6)
    assert img.getpixel((0, 0)) == (0, 255, 0, 255)
    # pristine markers survive resolve so IRPainter can still emit the path
    assert utils.get_pristine_image_asset_path(img) == path

    gone = AssetImageRef(path=tmp_path / "missing.png", size=(8, 6), mode="RGBA")
    placeholder = resolve_image_source_sync(gone)
    assert placeholder.size[0] > 0  # question-mark placeholder, not an exception


def test_imagebg_keeps_asset_ref_lazy_and_emits_region_relative_effects(tmp_path, monkeypatch):
    """ImageBg construction and IR lowering must never turn a healthy asset ref into mem pixels.

    The non-root region also pins the fact that ImageBg is a generic WidgetBg, not a scene-level
    whole-canvas background. Blur sigma follows the source->destination scale because Pillow
    applies GaussianBlur(3) before resizing.
    """
    from src.sekai.skia_renderer import ir_painter
    from src.sekai.skia_renderer.ir_painter import IRPainter
    from src.settings import DEFAULT_BOLD_FONT, DEFAULT_FONT, FONT_DIR

    path = tmp_path / "nested" / "bg.png"
    path.parent.mkdir()
    _noise_image(19, (20, 10)).save(path)
    stat = path.stat()
    ref = AssetImageRef(path=path, size=(20, 10), mode="RGBA", mtime_ns=stat.st_mtime_ns, file_size=stat.st_size)

    bg = plot.ImageBg(ref, align="br", mode="fit", blur=True, fade=0.1)
    assert bg.img is ref
    monkeypatch.setattr(
        ir_painter,
        "resolve_image_source_sync",
        lambda *_args, **_kwargs: pytest.fail("healthy ImageBg asset ref decoded in Python"),
    )

    painter = IRPainter(
        (60, 50),
        assets_base_dir=str(tmp_path),
        font_dir=str(FONT_DIR),
        default_font=DEFAULT_FONT,
        bold_font=DEFAULT_BOLD_FONT,
    )
    painter.set_region((11, 7), (30, 20))
    bg.draw(painter)
    painter.restore_region()
    scene, mem = painter.build_scene()

    assert mem == {}
    node = scene["root"]["children"][0]
    assert node["type"] == "Image"
    assert node["path"] == "nested/bg.png"
    assert node["pos"] == [1, 7]  # region offset + bottom-right fit offset (-10, 0)
    assert node["size"] == [40, 20]
    assert node["sampling"] == "catmull_rom"
    assert node["tint"] == {"color": [229, 229, 229, 255], "mode": "multiply", "strength": 1.0}
    assert node["blur_sigma"] == [6.0, 6.0]


def test_imagebg_fill_scales_blur_per_axis(tmp_path):
    from src.sekai.skia_renderer.ir_painter import IRPainter
    from src.settings import DEFAULT_BOLD_FONT, DEFAULT_FONT, FONT_DIR

    path = tmp_path / "bg.png"
    _noise_image(23, (20, 10)).save(path)
    stat = path.stat()
    ref = AssetImageRef(path=path, size=(20, 10), mode="RGBA", mtime_ns=stat.st_mtime_ns, file_size=stat.st_size)
    painter = IRPainter(
        (50, 30),
        assets_base_dir=str(tmp_path),
        font_dir=str(FONT_DIR),
        default_font=DEFAULT_FONT,
        bold_font=DEFAULT_BOLD_FONT,
    )

    plot.ImageBg(ref, mode="fill", blur=True, fade=0).draw(painter)
    node = painter.build_scene()[0]["root"]["children"][0]
    assert node["blur_sigma"] == [7.5, 9.0]


def test_imagebg_pillow_replay_preserves_legacy_effect_order():
    """Moving fade/blur out of the constructor must not move them after the resize."""
    source = _noise_image(29, (20, 10))
    painter = Painter(size=(60, 50))
    painter.set_region((11, 7), (30, 20))
    plot.ImageBg(source, align="br", mode="fit", blur=True, fade=0.1).draw(painter)
    painter.restore_region()
    actual = asyncio.run(painter.get())

    effected = source.filter(ImageFilter.GaussianBlur(radius=3))
    effected = ImageEnhance.Brightness(effected).enhance(0.9)
    resized = effected.resize((40, 20))
    expected = Image.new("RGBA", (60, 50), (0, 0, 0, 0))
    expected.paste(resized, (1, 7), resized)
    assert _max_channel_delta(actual, expected) == 0


def test_imagebox_layout_uses_ref_size_without_decode():
    ref = get_encoded_image_ref(_png_bytes((100, 50)))
    box = ImageBox(ref, size=(50, None), image_size_mode="fit")
    assert box._get_content_size() == (50, 25)
    assert box.image is ref  # still lazy after layout


def test_imagebox_asset_ref_crop_sampling_and_tint_stay_lazy_in_ir(tmp_path, monkeypatch):
    """ImageBox decorations must lower to one path-backed Image node, not a mem raster."""
    from src.sekai.skia_renderer import ir_painter
    from src.sekai.skia_renderer.ir_painter import IRPainter
    from src.settings import DEFAULT_BOLD_FONT, DEFAULT_FONT, FONT_DIR

    asset = tmp_path / "maps" / "site.png"
    asset.parent.mkdir()
    _noise_image(37, (20, 12)).save(asset)
    stat = asset.stat()
    ref = AssetImageRef(
        path=asset,
        size=(20, 12),
        mode="RGBA",
        mtime_ns=stat.st_mtime_ns,
        file_size=stat.st_size,
    )
    box = ImageBox(
        ref,
        size=(12, 8),
        image_size_mode="fill",
        source_rect=(2, 2, 14, 10),
        sampling="linear",
        tint=ImageTint((128, 64, 255, 255), mode="multiply"),
    )
    monkeypatch.setattr(
        ir_painter,
        "resolve_image_source_sync",
        lambda *_args, **_kwargs: pytest.fail("healthy decorated AssetImageRef decoded in Python"),
    )
    painter = IRPainter(
        (12, 8),
        assets_base_dir=str(tmp_path),
        font_dir=str(FONT_DIR),
        default_font=DEFAULT_FONT,
        bold_font=DEFAULT_BOLD_FONT,
    )

    box.draw(painter)
    scene, mem = painter.build_scene()

    assert mem == {}
    assert scene["root"]["children"] == [
        {
            "type": "Image",
            "pos": [0.0, 0.0],
            "size": [12, 8],
            "path": "maps/site.png",
            "fit": "stretch",
            "sampling": "linear",
            "alpha": 1.0,
            "tint": {"color": [128, 64, 255, 255], "mode": "multiply", "strength": 1.0},
            "source_rect": [2.0, 2.0, 14.0, 10.0],
        }
    ]


def test_canvas_renders_lazy_refs_via_pillow():
    ref = get_encoded_image_ref(_png_bytes((20, 20), (0, 0, 255, 255)))

    async def main():
        with Canvas(bg=FillBg((255, 255, 255, 255))) as canvas:
            with HSplit().set_sep(0):
                ImageBox(ref, size=(20, 20), image_size_mode="fill")
        return await canvas.get_img()

    img = asyncio.run(main())
    assert img.getpixel((10, 10)) == (0, 0, 255, 255)


def test_painter_clip_roundrect_masks_corners_and_keeps_center():
    async def main():
        p = Painter(size=(60, 60))
        p.push_clip_roundrect((10, 10), (40, 40), 12)
        p.rect((0, 0), (60, 60), fill=(255, 0, 0, 255))
        p.pop_clip()
        return await p.get()

    img = asyncio.run(main())
    assert img.getpixel((30, 30))[3] == 255  # center kept
    assert img.getpixel((11, 11))[3] == 0  # rounded corner clipped
    assert img.getpixel((5, 30))[3] == 0  # outside the clip rect entirely


def test_painter_unbalanced_clip_raises():
    async def main():
        p = Painter(size=(20, 20))
        p.push_clip_roundrect((0, 0), (20, 20), 4)
        return await p.get()

    with pytest.raises(AssertionError):
        asyncio.run(main())


def test_painter_push_mask_multiplies_the_layer_alpha_by_the_mask():
    """The Pillow half of the push_mask/pop_mask pair: the layer's alpha times the mask's alpha
    (Skia's DstIn — see test_ir_painter). Over an opaque layer that is exactly putalpha()."""
    mask = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    mask.paste(Image.new("RGBA", (20, 40), (255, 255, 255, 255)), (0, 0))  # left half opaque
    mask.paste(Image.new("RGBA", (10, 40), (255, 255, 255, 128)), (20, 0))  # then half-transparent

    async def main():
        p = Painter(size=(40, 40))
        p.push_mask(mask, (0, 0), (40, 40))
        p.rect((0, 0), (40, 40), fill=(255, 0, 0, 255))
        p.pop_mask()
        return await p.get()

    img = asyncio.run(main())
    assert img.getpixel((10, 20)) == (255, 0, 0, 255)  # mask opaque -> layer untouched
    assert img.getpixel((25, 20))[3] == 128  # mask half -> alpha multiplied
    assert img.getpixel((35, 20))[3] == 0  # mask empty -> masked away


def test_painter_unbalanced_mask_raises():
    async def main():
        p = Painter(size=(20, 20))
        p.push_mask(Image.new("RGBA", (20, 20), (255, 255, 255, 255)), (0, 0), (20, 20))
        return await p.get()

    with pytest.raises(AssertionError):
        asyncio.run(main())


def test_painter_paste_src_writes_all_four_channels_verbatim():
    """paste_src is Porter-Duff Src: it must carry the rgb hiding under fully transparent pixels,
    because Pillow's paste-lerp reads it back when a later overlay's AA edge crosses them (the
    honor badge frame over its base art's transparent corners)."""
    art = Image.new("RGBA", (20, 20), (200, 30, 90, 0))  # rgb under zero alpha
    art.paste(Image.new("RGBA", (10, 20), (10, 20, 30, 255)), (0, 0))

    async def main():
        p = Painter(size=(20, 20))
        p.paste_src(art, (0, 0))
        return await p.get()

    img = asyncio.run(main())
    assert img.getpixel((5, 10)) == (10, 20, 30, 255)
    assert img.getpixel((15, 10)) == (200, 30, 90, 0)  # verbatim, not zeroed by a src-over

    async def blended():
        p = Painter(size=(20, 20))
        p.paste_with_alpha_blend(art, (0, 0))
        return await p.get()

    assert asyncio.run(blended()).getpixel((15, 10)) == (0, 0, 0, 0)  # the difference paste_src exists for


def test_painter_shadow_roundrect_draws_blur():
    async def main():
        p = Painter(size=(60, 60))
        p.shadow_roundrect((20, 20), (20, 20), 5, shadow_width=6, shadow_alpha=0.5)
        return await p.get()

    img = asyncio.run(main())
    assert img.getpixel((30, 30))[3] > 0  # shadow body
    assert img.getpixel((1, 1))[3] == 0  # far corner untouched


def test_asset_ref_resize_goes_through_global_cache(tmp_path):
    path = tmp_path / "b.png"
    Image.new("RGBA", (16, 12), (7, 8, 9, 255)).save(path)
    stat = path.stat()
    ref = AssetImageRef(path=path, size=(16, 12), mode="RGBA", mtime_ns=stat.st_mtime_ns, file_size=stat.st_size)
    img = resolve_image_source_sync(ref, target_size=(8, 6))
    assert img.size == (8, 6)
    again = resolve_image_source_sync(ref, target_size=(8, 6))
    assert again.size == (8, 6)


def test_prefetch_keeps_the_size_hint_when_a_widget_also_lists_its_own_image(tmp_path):
    """A widget may name its own ``image`` among ``prefetch_image_sources`` — CardFullThumbnailBox
    does, passing ``layers.base`` to super().__init__ *and* listing it first among the extras. Both
    entries key on ``id(ref)``, so an assignment in the extras loop overwrites the display-size hint
    recorded for the ImageBox and the asset gets prefetched (and cached) at full source size instead.

    On card/box that is 1404 thumbnails decoded at source size: 88 MB instead of 13 MB, a thumbnail
    cache thrashing at a 0% hit rate, and +700 MB of peak RSS at 8 concurrent renders."""
    path = tmp_path / "thumb.png"
    Image.new("RGBA", (256, 256), (1, 2, 3, 255)).save(path)
    stat = path.stat()
    ref = AssetImageRef(path=path, size=(256, 256), mode="RGBA", mtime_ns=stat.st_mtime_ns, file_size=stat.st_size)

    class _SelfListingBox(ImageBox):
        def __init__(self) -> None:
            super().__init__(ref, size=(48, 48), image_size_mode="fit")
            self.prefetch_image_sources = [ref]  # the same object, exactly as CardFullThumbnailBox does

    refs: dict = {}
    plot._collect_asset_refs(_SelfListingBox(), refs)

    assert [hint for _, hint, _ in refs.values()] == [(48, 48)], "the extras loop clobbered the display-size hint"


def test_prefetch_keeps_sampling_in_resize_cache_identity(tmp_path, monkeypatch):
    """Linear and cubic consumers of one ref need separate, correctly warmed resize entries."""
    path = tmp_path / "shared.png"
    Image.new("RGBA", (64, 64), (1, 2, 3, 255)).save(path)
    stat = path.stat()
    ref = AssetImageRef(path=path, size=(64, 64), mode="RGBA", mtime_ns=stat.st_mtime_ns, file_size=stat.st_size)
    root = HSplit()
    root.add_item(ImageBox(ref, size=(24, 24), image_size_mode="fill", sampling="linear"))
    root.add_item(ImageBox(ref, size=(24, 24), image_size_mode="fill", sampling="catmull_rom"))
    calls = []

    def fake_resolve(source, target_size, resample):
        calls.append((source, target_size, resample))
        return Image.new("RGBA", target_size)

    async def fake_run_in_pool(func, *args):
        return func(*args)

    monkeypatch.setattr(plot, "resolve_image_source_sync", fake_resolve)
    monkeypatch.setattr(plot, "run_in_pool", fake_run_in_pool)

    asyncio.run(plot.prefetch_asset_refs(root))

    assert [(target, resample) for _, target, resample in calls] == [
        ((24, 24), Image.Resampling.BILINEAR),
        ((24, 24), Image.Resampling.BICUBIC),
    ]


def _max_channel_delta(a: Image.Image, b: Image.Image) -> int:
    """Largest per-channel difference. NOT ImageChops.difference(...).getbbox(): getbbox
    keys off alpha, and a difference image of two opaque renders has alpha 0 everywhere,
    so it reports "empty" no matter how far apart the RGB channels are."""
    diff = ImageChops.difference(a.convert("RGBA"), b.convert("RGBA"))
    return max(max(px) for px in diff.get_flattened_data())


def _noise_image(seed: int, size: tuple[int, int] = (64, 64)) -> Image.Image:
    """Asymmetric high-frequency content. Bilinear and bicubic agree exactly on a linear
    ramp, and on a checkerboard both average to the same flat gray — either would make a
    resample-sensitive test pass vacuously."""
    rng = random.Random(seed)
    img = Image.new("RGBA", size)
    img.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256), 255) for _ in range(size[0] * size[1])])
    return img


def test_ref_paste_resamples_identically_to_a_decoded_paste(tmp_path):
    """A lazy source must not change the pixels: Painter resizes a decoded PIL image with
    Image.resize() (Pillow default BICUBIC), so the ref path has to use the same filter."""
    _noise_image(11).save(tmp_path / "noise.png")

    async def paste(source):
        p = Painter(size=(25, 25))
        p.paste(source, (0, 0), (25, 25))
        return await p.get()

    async def main():
        ref = await get_asset_image_ref(tmp_path, "noise.png")
        decoded = await get_img_from_path(tmp_path, "noise.png")
        return await paste(decoded), await paste(ref)

    from_decoded, from_ref = asyncio.run(main())
    assert _max_channel_delta(from_decoded, from_ref) == 0


def test_resize_cache_keys_on_the_resample_filter(tmp_path):
    """Two different filters at the same target size must not alias in the global cache."""
    path = tmp_path / "noise2.png"
    _noise_image(7).save(path)
    stat = path.stat()
    ref = AssetImageRef(path=path, size=(64, 64), mode="RGBA", mtime_ns=stat.st_mtime_ns, file_size=stat.st_size)

    bilinear = resolve_image_source_sync(ref, target_size=(25, 25), resample=Image.Resampling.BILINEAR)
    bicubic = resolve_image_source_sync(ref, target_size=(25, 25), resample=Image.Resampling.BICUBIC)
    assert _max_channel_delta(bilinear, bicubic) > 0  # the second call was not served the first's entry


def _two_tone_ref(tmp_path, name="half.png") -> AssetImageRef:
    """20x10 asset: left half red, right half green."""
    src = Image.new("RGBA", (20, 10), (255, 0, 0, 255))
    src.paste((0, 255, 0, 255), (10, 0, 20, 10))
    path = tmp_path / name
    src.save(path)
    stat = path.stat()
    return AssetImageRef(path=path, size=(20, 10), mode="RGBA", mtime_ns=stat.st_mtime_ns, file_size=stat.st_size)


def test_painter_paste_src_rect_crops_before_fit(tmp_path):
    ref = _two_tone_ref(tmp_path)

    async def main():
        p = Painter(size=(8, 8))
        p.paste(ref, (0, 0), (8, 8), src_rect=(10, 0, 20, 10))
        return await p.get()

    img = asyncio.run(main())
    assert img.getpixel((4, 4)) == (0, 255, 0, 255)  # green slice; red half cropped away


def test_painter_paste_with_alpha_blend_src_rect(tmp_path):
    ref = _two_tone_ref(tmp_path)

    async def main():
        p = Painter(size=(8, 8))
        p.paste_with_alpha_blend(ref, (0, 0), (8, 8), src_rect=(0, 0, 10, 10))
        return await p.get()

    img = asyncio.run(main())
    assert img.getpixel((4, 4)) == (255, 0, 0, 255)  # red slice this time


def test_painter_crop_bilinear_multiply_matches_legacy_pixel_pipeline(tmp_path):
    """The shared decoration order is crop -> resize -> tint -> paste on Pillow."""
    source = _noise_image(41, (19, 15))
    path = tmp_path / "site.png"
    source.save(path)
    stat = path.stat()
    ref = AssetImageRef(
        path=path,
        size=source.size,
        mode="RGBA",
        mtime_ns=stat.st_mtime_ns,
        file_size=stat.st_size,
    )
    source_rect = (3, 2, 17, 13)
    target_size = (23, 17)
    color = (119, 211, 73, 255)

    async def render():
        painter = Painter(size=target_size)
        painter.paste(
            ref,
            (0, 0),
            target_size,
            src_rect=source_rect,
            sampling="linear",
            tint=ImageTint(color, mode="multiply"),
        )
        return await painter.get()

    actual = asyncio.run(render())
    transformed = source.crop(source_rect).resize(target_size, Image.Resampling.BILINEAR)
    transformed = multiply_image_by_color(transformed, color)
    expected = Image.new("RGBA", target_size, (0, 0, 0, 0))
    expected.paste(transformed, (0, 0), transformed)

    assert _max_channel_delta(actual, expected) == 0


def test_painter_recolor_preserves_source_alpha_and_alpha_composite_result(tmp_path):
    source = Image.new("RGBA", (4, 2))
    source.putdata(
        [
            (9, 17, 31, 0),
            (40, 50, 60, 32),
            (70, 80, 90, 128),
            (100, 110, 120, 255),
            (130, 140, 150, 224),
            (160, 170, 180, 96),
            (190, 200, 210, 1),
            (220, 230, 240, 200),
        ]
    )
    path = tmp_path / "mark.png"
    source.save(path)
    stat = path.stat()
    ref = AssetImageRef(
        path=path,
        size=source.size,
        mode="RGBA",
        mtime_ns=stat.st_mtime_ns,
        file_size=stat.st_size,
    )
    tint = ImageTint((255, 32, 32, 255), mode="recolor")

    async def render_src():
        painter = Painter(size=source.size)
        painter.paste_src(ref, (0, 0), tint=tint)
        return await painter.get()

    async def render_composited():
        painter = Painter(size=(8, 6))
        painter.rect((0, 0), (8, 6), fill=(11, 29, 47, 255))
        painter.paste_with_alpha_blend(ref, (2, 2), tint=tint)
        return await painter.get()

    recolored = Image.new("RGBA", source.size, (255, 32, 32, 255))
    recolored.putalpha(source.getchannel("A"))
    actual_src = asyncio.run(render_src())
    assert list(actual_src.getchannel("A").get_flattened_data()) == list(source.getchannel("A").get_flattened_data())
    assert _max_channel_delta(actual_src, recolored) == 0

    expected_composited = Image.new("RGBA", (8, 6), (11, 29, 47, 255))
    expected_composited.alpha_composite(recolored, (2, 2))
    assert _max_channel_delta(asyncio.run(render_composited()), expected_composited) == 0


def test_painter_multiply_tint_scales_implicit_rgb_alpha():
    """RGB has implicit alpha=255; multiply must modulate it like Skia's filter does."""

    async def render():
        painter = Painter(size=(2, 1))
        painter.paste_src(
            Image.new("RGB", (2, 1), (120, 80, 40)),
            (0, 0),
            tint=ImageTint((128, 64, 255, 96), mode="multiply"),
        )
        return await painter.get()

    assert list(asyncio.run(render()).get_flattened_data()) == [
        (60, 20, 40, 96),
        (60, 20, 40, 96),
    ]


def test_painter_paste_with_alpha_blend_shadow_uses_pre_tint_silhouette(tmp_path):
    """The drop shadow derives from the source alpha BEFORE tinting (matching _impl_paste
    and the Rust untinted-silhouette shadow), so a tint must not move or fade it."""
    source = Image.new("RGBA", (10, 8), (200, 40, 40, 255))
    path = tmp_path / "badge.png"
    source.save(path)
    stat = path.stat()
    ref = AssetImageRef(
        path=path,
        size=source.size,
        mode="RGBA",
        mtime_ns=stat.st_mtime_ns,
        file_size=stat.st_size,
    )
    interior = (10, 9, 20, 17)  # pos + source size

    def render(tint):
        async def go():
            painter = Painter(size=(30, 26))
            painter.rect((0, 0), (30, 26), fill=(255, 255, 255, 255))
            painter.paste_with_alpha_blend(ref, (10, 9), use_shadow=True, shadow_width=6, tint=tint)
            return await painter.get()

        return asyncio.run(go())

    untinted = render(None)
    tinted = render(ImageTint((32, 32, 255, 128), mode="recolor"))

    assert _max_channel_delta(untinted.crop(interior), tinted.crop(interior)) > 0  # the tint drew
    cover = Image.new("RGBA", source.size, (0, 0, 0, 255))
    masked_untinted, masked_tinted = untinted.copy(), tinted.copy()
    masked_untinted.paste(cover, interior[:2])
    masked_tinted.paste(cover, interior[:2])
    assert _max_channel_delta(masked_untinted, masked_tinted) == 0  # shadow ring untouched by tint


def test_painter_paste_src_sampling_selects_the_resize_kernel(tmp_path):
    """``sampling`` must reach the actual resize on paste_src (via the ref resize-cache path),
    and ``nearest`` must behave as PIL NEAREST — neither was exercised anywhere else."""
    source = Image.new("RGBA", (2, 2))
    source.putdata(
        [
            (255, 255, 255, 255),
            (0, 0, 0, 255),
            (0, 0, 0, 255),
            (255, 255, 255, 255),
        ]
    )
    path = tmp_path / "checker.png"
    source.save(path)
    stat = path.stat()
    ref = AssetImageRef(
        path=path,
        size=source.size,
        mode="RGBA",
        mtime_ns=stat.st_mtime_ns,
        file_size=stat.st_size,
    )

    def render(sampling):
        async def go():
            painter = Painter(size=(8, 8))
            painter.paste_src(ref, (0, 0), (8, 8), sampling=sampling)
            return await painter.get()

        return asyncio.run(go())

    nearest = render("nearest")
    linear = render("linear")
    assert _max_channel_delta(nearest, source.resize((8, 8), Image.Resampling.NEAREST)) == 0
    assert _max_channel_delta(linear, source.resize((8, 8), Image.Resampling.BILINEAR)) == 0
    assert _max_channel_delta(nearest, linear) > 0
