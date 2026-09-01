from __future__ import annotations

from io import BytesIO

from PIL import Image
import pytest

from src.sekai.base import painter


def _image(size=(8, 4), mode="RGBA") -> Image.Image:
    color = (20, 40, 60, 200) if mode == "RGBA" else (20, 40, 60)
    return Image.new(mode, size, color)


def test_image_background_placements_cover_every_mode_and_alignment() -> None:
    assert painter._image_bg_axis_offset(10, 4, "c", "l") == 3
    assert painter._image_bg_axis_offset(10, 4, "l", "l") == 0
    assert painter._image_bg_axis_offset(10, 4, "r", "l") == 6

    fit = list(painter._iter_image_bg_placements((100, 50), (20, 20), "c", "fit"))
    assert fit == [((0, -25), (100, 100), (5.0, 5.0), True)]
    assert next(painter._iter_image_bg_placements((100, 50), (20, 20), "c", "fill"))[1] == (100, 50)
    assert next(painter._iter_image_bg_placements((100, 50), (20, 20), "br", "fixed"))[0] == (80, 30)
    assert len(list(painter._iter_image_bg_placements((45, 41), (20, 20), "c", "repeat"))) == 9
    with pytest.raises(ValueError, match="unsupported image background mode"):
        list(painter._iter_image_bg_placements((10, 10), (2, 2), "c", "bad"))


def test_crop_color_and_resize_helpers_cover_all_modes() -> None:
    assert painter.crop_by_align((10, 8), (4, 2), "tl") == (0, 0, 4, 2)
    assert painter.crop_by_align((10, 8), (4, 2), "br") == (6, 6, 10, 8)
    assert painter.crop_by_align((10, 8), (4, 2), "c") == (3, 3, 7, 5)
    with pytest.raises(AssertionError, match="Crop width"):
        painter.crop_by_align((2, 2), (3, 1), "c")
    with pytest.raises(AssertionError, match="Crop height"):
        painter.crop_by_align((2, 2), (1, 3), "c")

    assert painter.color_code_to_rgb("#abc") == (160, 176, 192, 255)
    assert painter.color_code_to_rgb("112233") == (17, 34, 51, 255)
    with pytest.raises(ValueError, match="Invalid color code"):
        painter.color_code_to_rgb("12")
    assert painter.rgb_to_color_code((17, 34, 51, 99)) == "#112233"
    assert painter.lerp_color((0, 100, 255), (255, 0, 255), 0.5) == (127, 50, 255)
    assert painter.adjust_color((1, 2, 3), r=4, g=5, b=6, a=7) == (4, 5, 6, 7)
    assert painter.adjust_color((1, 2, 3, 4)) == (1, 2, 3, 4)

    wide = _image((8, 4))
    tall = _image((4, 8))
    expected = {
        "long": (4, 2),
        "short": (8, 4),
        "w": (4, 2),
        "h": (8, 4),
        "wxh": (2, 1),
        "scale": (32, 16),
    }
    for mode, size in expected.items():
        assert painter.resize_keep_ratio(wide, 4, mode).size == size
    assert painter.resize_keep_ratio(tall, 4, "long").size == (2, 4)
    assert painter.resize_keep_ratio(tall, 4, "short").size == (4, 8)
    assert painter.resize_keep_ratio(wide, 2, "w", scale=2).size == (4, 2)
    with pytest.raises(ValueError, match="Invalid mode"):
        painter.resize_keep_ratio(wide, 4, "bad")

    assert painter.resize_by_optional_size(wide, (None, None)) is wide
    assert painter.resize_by_optional_size(wide, (None, 4)) is wide
    assert painter.resize_by_optional_size(wide, (8, None)) is wide
    assert painter.resize_by_optional_size(wide, (4, None)).size == (4, 2)
    assert painter.resize_by_optional_size(wide, (None, 2)).size == (4, 2)
    assert painter.resize_by_optional_size(wide, (8, 4)) is wide
    assert painter.resize_by_optional_size(wide, (3, 3)).size == (3, 3)


def test_gradient_generation_masks_and_validation_cover_rgb_and_rgba() -> None:
    with pytest.raises(NotImplementedError):
        painter.Gradient().get_colors((2, 2))
    with pytest.raises(AssertionError, match="same point"):
        painter.LinearGradient((0, 0, 0), (1, 1, 1), (0, 0), (0, 0))
    with pytest.raises(AssertionError):
        painter.LinearGradient((0, 0, 0), (1, 1, 1), (0, 0), (1, 1), "bad")

    combine = painter.LinearGradient((0, 0, 0), (255, 255, 255), (0, 0), (1, 0))
    separate = painter.LinearGradient((0, 0, 0, 0), (255, 255, 255, 255), (0, 0), (1, 1), "separate")
    assert combine.get_colors((3, 2)).shape == (2, 3, 3)
    assert separate.get_colors((3, 2)).shape == (2, 3, 4)
    assert combine.get_img((3, 2)).mode == "RGBA"
    rgba_mask = Image.new("RGBA", (3, 2), (0, 0, 0, 128))
    assert separate.get_img((3, 2), rgba_mask).getchannel("A").getextrema() == (128, 128)
    with pytest.raises(AssertionError, match="Mask size"):
        combine.get_img((3, 2), Image.new("L", (1, 1)))

    radial = painter.RadialGradient((0, 0, 0, 0), (255, 255, 255, 255), (0.5, 0.5), 2)
    assert radial.get_colors((4, 4)).shape == (4, 4, 4)


def test_image_tint_validation_sampling_and_recolor_paths() -> None:
    with pytest.raises(ValueError, match="3 or 4"):
        painter.ImageTint((1, 2))
    with pytest.raises(ValueError, match="unsupported image tint mode"):
        painter.ImageTint((1, 2, 3), "bad")
    assert painter._apply_image_tint(_image(), None).size == (8, 4)
    assert painter._apply_image_tint(_image(mode="RGB"), painter.ImageTint((255, 0, 0), "multiply")).mode == "RGBA"
    recolored = painter._apply_image_tint(_image(), painter.ImageTint((1, 2, 3, 128), "recolor"))
    assert recolored.getpixel((0, 0)) == (1, 2, 3, 100)
    assert painter.pillow_resample_for_image_sampling(None) == painter.PASTE_RESAMPLE
    assert painter.pillow_resample_for_image_sampling("nearest") == Image.Resampling.NEAREST


def test_emoji_byte_cache_covers_memory_disk_eviction_empty_and_fail_open(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(painter, "PAINTER_EMOJI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(painter, "PAINTER_EMOJI_CACHE_MAX_ENTRIES", 1)
    painter._painter_emoji_bytes_cache.clear()
    key = painter._emoji_source_cache_key("emoji", "🙂")
    assert key.startswith("google_emoji_")
    assert painter._get_emoji_source_bytes_cached(key) is None

    painter._put_emoji_source_bytes_cached(key, b"one")
    assert painter._get_emoji_source_bytes_cached(key) == b"one"
    second = painter._emoji_source_cache_key("emoji", "x")
    painter._put_emoji_source_bytes_cached(second, b"two")
    assert list(painter._painter_emoji_bytes_cache) == [second]

    painter._painter_emoji_bytes_cache.clear()
    assert painter._get_emoji_source_bytes_cached(second) == b"two"
    empty = painter._emoji_source_cache_path("empty")
    open(empty, "wb").close()
    assert painter._get_emoji_source_bytes_cached("empty") is None
    painter._put_emoji_source_bytes_cached("ignored", b"")

    monkeypatch.setattr(painter.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("disk")))
    painter._put_emoji_source_bytes_cached("failed", b"data")


def test_cached_google_emoji_source_covers_cache_fetch_none_error_and_session_close(monkeypatch) -> None:
    source = painter.CachedGoogleEmojiSource()
    monkeypatch.setattr(painter, "_get_emoji_source_bytes_cached", lambda _key: b"cached")
    assert source._get_cached_stream("emoji", "x", lambda _source: None).read() == b"cached"

    monkeypatch.setattr(painter, "_get_emoji_source_bytes_cached", lambda _key: None)
    stored: list[bytes] = []
    monkeypatch.setattr(painter, "_put_emoji_source_bytes_cached", lambda _key, data: stored.append(data))
    stream = source._get_cached_stream("emoji", "x", lambda _source: BytesIO(b"fresh"))
    assert stream is not None
    assert stream.read() == b"fresh"
    assert stored == [b"fresh"]
    assert source._get_cached_stream("emoji", "x", lambda _source: None) is None
    assert source._get_cached_stream("emoji", "x", lambda _source: (_ for _ in ()).throw(RuntimeError())) is None


def test_font_bbox_cache_and_text_metrics_cover_hits_clear_and_emoji(monkeypatch) -> None:
    font = painter.ImageFont.load_default()
    painter._text_bbox_cache.clear()
    first = painter._measure_bbox(font, "abc")
    assert painter._measure_bbox(font, "abc") is first
    monkeypatch.setattr(painter, "_TEXT_BBOX_CACHE_MAX", 1)
    painter._measure_bbox(font, "def")
    assert len(painter._text_bbox_cache) == 1
    assert painter.get_text_offset(font, "abc") == painter._measure_bbox(font, "abc")[:2]
    assert painter.get_text_size(font, "abc")[0] > 0

    monkeypatch.setattr(painter.emoji, "emoji_count", lambda _text: 1)
    monkeypatch.setattr(painter, "getsize_emoji", lambda _text, font: (12, 13))
    painter._text_emoji_size_cache.clear()
    assert painter.get_text_size(font, "🙂") == (12, 13)
    assert painter.get_text_size(font, "🙂") == (12, 13)
