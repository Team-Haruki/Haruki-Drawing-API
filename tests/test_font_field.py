"""BASIC A8 glyph data preserves the old mask, including missing and empty ink."""

from concurrent.futures import ThreadPoolExecutor
import math
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont
import pytest

from src.core.pillow_telemetry import begin_pillow_touch_scope, end_pillow_touch_scope, take_pillow_touch_snapshot
from src.sekai.profile.custom_profile import font_field

ROOT = Path(__file__).resolve().parents[1]
FONT = ROOT / "data/SourceHanSansSC-Regular.otf"
TEXTS = ["Ag日本語", "AVATAR", "j", "🙂", "𠮷", "á", "́", "\u200d", "\u200b", "\t", " ", ""]


@pytest.fixture
def native():
    module = pytest.importorskip("haruki_skia_renderer")
    if getattr(module, "TEXT_MASK_CAPABILITY", 0) < 1:
        pytest.skip("native BASIC mask API required")
    return module


@pytest.fixture
def font():
    if not FONT.is_file():
        pytest.skip("fixture font required")
    return FONT


def _legacy(path, text, size):
    font = ImageFont.truetype(str(path), max(1, round(size)), layout_engine=ImageFont.Layout.BASIC)
    box = font.getbbox(text)
    width, height = box[2] - box[0], box[3] - box[1]
    if not width or not height:
        return (1, 1), b"\0", box
    image = Image.new("L", (width, height), 0)
    ImageDraw.Draw(image).text((-box[0], -box[1]), text, font=font, fill=255)
    return image.size, image.tobytes(), box


@pytest.mark.parametrize("size", [1, 7.5, 12.5, 45, 90, 150.3])
def test_native_masks_match_legacy_bytes(native, font, monkeypatch, size):
    expected = {text: _legacy(font, text, size) for text in TEXTS}

    def unexpected(*args, **kwargs):
        pytest.fail("Pillow mask reached")

    monkeypatch.setattr(ImageDraw, "Draw", unexpected)
    token = begin_pillow_touch_scope()
    try:
        for text in TEXTS:
            field, box = font_field.basic_text_field(font, text, size)
            assert (field.size, field.pixels, box) == expected[text], text
        assert take_pillow_touch_snapshot().counts == {}
    finally:
        end_pillow_touch_scope(token)


@pytest.mark.parametrize("size", [0, -1, math.inf, math.nan, 2049])
def test_mask_rejects_invalid_size_before_font_access(native, size):
    with pytest.raises(ValueError, match="size"):
        native.basic_text_mask("", "absent.ttf", "A", size, 100)


@pytest.mark.parametrize("limit", [0, 16 * 1024 * 1024 + 1])
def test_mask_rejects_invalid_budget(native, limit):
    with pytest.raises(ValueError, match="pixel limit"):
        native.basic_text_mask("", "absent.ttf", "A", 90, limit)


def test_mask_rejects_oversized_text(native, font):
    with pytest.raises(ValueError, match=r"character|scalar"):
        native.basic_text_mask("", str(font), "A" * 16385, 90, 100)
    with pytest.raises(ValueError, match="dimension limit"):
        native.basic_text_mask("", str(font), "A" * 4000, 90, 16 * 1024 * 1024)


def test_mask_pixel_rejection_does_not_retry_pillow(native, font, monkeypatch):
    from src.sekai.profile.custom_profile import pillow_fields

    def unexpected(*args, **kwargs):
        pytest.fail("allocation rejection retried Pillow")

    monkeypatch.setattr(pillow_fields, "basic_text_field", unexpected)
    with pytest.raises(ValueError, match="pixel limit"):
        font_field.basic_text_field(font, "A", 90, max_pixels=1)


@pytest.mark.parametrize("failure", ["missing", "stale", "broken"])
def test_mask_recovery_stays_explicit_and_uncached(native, font, monkeypatch, failure):
    def fail(*args):
        raise RuntimeError("broken mask")

    def module(name):
        if failure == "missing":
            raise ImportError("missing wheel")
        return SimpleNamespace(TEXT_MASK_CAPABILITY=0 if failure == "stale" else 1, basic_text_mask=fail)

    monkeypatch.setattr(font_field, "import_module", module)
    for _ in range(2):
        token = begin_pillow_touch_scope()
        try:
            field, box = font_field.basic_text_field(font, "A", 45)
            assert (field.size, field.pixels, box) == _legacy(font, "A", 45)
            assert take_pillow_touch_snapshot().native_purity == "hybrid"
        finally:
            end_pillow_touch_scope(token)


def test_masks_are_consistent_across_threads_and_sizes(native, font):
    inputs = [(text, size) for text in TEXTS for size in (12, 45, 90)]
    expected = {(text, size): _legacy(font, text, size) for text, size in inputs}

    def render(item):
        text, size = item
        field, box = font_field.basic_text_field(font, text, size)
        assert (field.size, field.pixels, box) == expected[item]

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(render, inputs * 3))
