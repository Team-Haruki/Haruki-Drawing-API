"""Unified ImageSource: lazy refs through layout, Pillow decode-on-demand, clip/shadow ops."""

from __future__ import annotations

import asyncio
from io import BytesIO
import random

from PIL import Image, ImageChops
import pytest

from src.sekai.base import plot, utils
from src.sekai.base.painter import Painter
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


def test_imagebox_layout_uses_ref_size_without_decode():
    ref = get_encoded_image_ref(_png_bytes((100, 50)))
    box = ImageBox(ref, size=(50, None), image_size_mode="fit")
    assert box._get_content_size() == (50, 25)
    assert box.image is ref  # still lazy after layout


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

    assert [hint for _, hint in refs.values()] == [(48, 48)], "the extras loop clobbered the display-size hint"


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


def test_process_pool_predispatch_materializes_refs(tmp_path):
    from src.sekai.base.painter import _resolve_op_image_sources

    path = tmp_path / "c.png"
    Image.new("RGBA", (10, 8), (1, 2, 3, 255)).save(path)
    stat = path.stat()
    ref = AssetImageRef(path=path, size=(10, 8), mode="RGBA", mtime_ns=stat.st_mtime_ns, file_size=stat.st_size)

    p = Painter(size=(30, 30))
    p.paste(ref, (0, 0), (20, 16))
    p.paste_with_alpha_blend(ref, (0, 0), None, 0.5)
    p.rect((0, 0), (5, 5), fill=(0, 0, 0, 255))
    _resolve_op_image_sources(p.operations)

    materialized = [op.args[0] for op in p.operations[:2]]
    assert all(isinstance(m, Image.Image) for m in materialized)
    assert materialized[0].size == (20, 16)  # resized via the global cache pre-dispatch
    assert materialized[1].size == (10, 8)  # no target size -> full-size decode
    assert p.operations[2].args[0] == (0, 0)  # non-paste ops untouched


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


def test_process_pool_predispatch_ships_full_pixels_for_src_rect(tmp_path):
    from src.sekai.base.painter import _resolve_op_image_sources

    ref = _two_tone_ref(tmp_path)
    p = Painter(size=(30, 30))
    p.paste(ref, (0, 0), (8, 8), src_rect=(10, 0, 20, 10))
    _resolve_op_image_sources(p.operations)

    # Crop happens in the worker-side impl, so the shipped pixels must be the full asset.
    assert isinstance(p.operations[0].args[0], Image.Image)
    assert p.operations[0].args[0].size == (20, 10)
