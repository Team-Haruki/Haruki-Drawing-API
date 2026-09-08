"""Pillow is the oracle, never an implementation dependency of native BASIC text."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytest

from src.sekai.skia_renderer.ir_builder import IRBuilder

try:
    import haruki_skia_renderer as native
except ImportError:
    native = None

FONT_DIR = Path(__file__).resolve().parents[1] / "data"
FONTS = [FONT_DIR / f"SourceHanSansSC-{weight}.otf" for weight in ("Regular", "Bold", "Heavy")]
TEXTS = [
    "Haruki",
    "AVATAR",
    "To",
    "未来",
    "哇",
    " ",
    "a ",
    "j",
    "é",
    "office",
    "",
    "日本語テスト",
    "你好 abc 123",
    "e\u0301",
]
pytestmark = pytest.mark.skipif(
    native is None or getattr(native, "TEXT_METRICS_CAPABILITY", 0) < 2 or not all(p.is_file() for p in FONTS),
    reason="native BASIC text and Source Han fixture fonts required",
)


@pytest.mark.parametrize("path", FONTS, ids=lambda p: p.stem)
@pytest.mark.parametrize("size", [8, 12, 16, 20, 24, 32, 48])
def test_basic_metrics_match_pillow(path, size):
    font = ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.BASIC)
    measured = native.measure_text_batch("", str(path), [(text, size) for text in TEXTS], engine="freetype_basic")
    for text, result in zip(TEXTS, measured, strict=True):
        assert result["advance"] == font.getlength(text), text
        assert result["pillow_bbox"] == font.getbbox(text), text
        assert (result["ascent"], result["descent"]) == font.getmetrics(), text


def render_text(
    path,
    size,
    text,
    *,
    engine="freetype_basic",
    background=(255, 255, 255, 255),
    color=(0, 0, 0, 255),
    pos=(15, 65),
    mask_lerp=False,
):
    builder = IRBuilder(
        600, 100, assets_base_dir=str(FONT_DIR), font_dir=str(FONT_DIR), default_font=path.name, bold_font=path.name
    )
    builder.rect((0, 0), (600, 100), fill=background)
    builder.text(text, pos, "default", size, baseline="alphabetic", fill=color, engine=engine, mask_lerp=mask_lerp)
    payload = native.render_scene(json.dumps(builder.build()).encode(), {})
    return Image.open(BytesIO(payload["image_bytes"])).convert("RGBA")


@pytest.mark.parametrize("path", FONTS, ids=lambda p: p.stem)
@pytest.mark.parametrize("size", [12, 20, 32, 48])
@pytest.mark.parametrize("background", [(255, 255, 255, 255), (27, 51, 79, 255), (0, 0, 0, 0)])
def test_basic_glyph_masks_match_pillow(path, size, background):
    text = "未来 Haruki AVATAR j é e\u0301"
    font = ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.BASIC)
    expected = Image.new("RGBA", (600, 100), background)
    ImageDraw.Draw(expected).text((15, 65), text, font=font, anchor="ls", fill=(0, 0, 0, 255))
    actual = render_text(path, size, text, background=background)
    diff = np.abs(np.asarray(expected).astype(int) - np.asarray(actual).astype(int))
    assert diff.max() <= 1, (path, size, background, diff.max(), diff.mean())


@pytest.mark.parametrize("color", [(24, 38, 58, 255), (30, 45, 66, 160)])
def test_mask_lerp_text_preserves_rgba_on_translucent_panels(color):
    background = (255, 255, 255, 112)
    text = "未来 Haruki AVATAR"
    path = FONTS[0]
    font = ImageFont.truetype(str(path), 24, layout_engine=ImageFont.Layout.BASIC)
    expected = Image.new("RGBA", (600, 100), background)
    ImageDraw.Draw(expected).text((15, 65), text, font=font, anchor="ls", fill=color)
    actual = render_text(path, 24, text, background=background, color=color, mask_lerp=True)
    diff = np.abs(np.asarray(expected).astype(int) - np.asarray(actual).astype(int))
    assert diff.max() <= 2
    assert np.array_equal(np.asarray(expected)[:, :, 3], np.asarray(actual)[:, :, 3])


def test_mask_lerp_text_rejects_masked_layer_before_loading_assets():
    builder = IRBuilder(
        100,
        50,
        assets_base_dir=str(FONT_DIR),
        font_dir=str(FONT_DIR),
        default_font=FONTS[0].name,
        bold_font=FONTS[0].name,
    )
    with builder.group((0, 0), (100, 50), mask="missing.png"):
        builder.text("未来", (5, 30), "default", 20, engine="freetype_basic", mask_lerp=True)
    with pytest.raises(RuntimeError, match="mask_lerp Text needs an identity surface"):
        native.render_scene(json.dumps(builder.build()).encode(), {})


def test_basic_metrics_are_thread_local_and_size_independent():
    requests = [(str(path), size) for path in FONTS for size in (12, 32, 20)] * 3

    def measure(request):
        path, size = request
        result = native.measure_text_batch("", path, [("未来 AVATAR", size)], engine="freetype_basic")[0]
        font = ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)
        return result["pillow_bbox"] == font.getbbox("未来 AVATAR")

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(measure, requests))


def test_shared_metric_proxy_fails_open_when_native_measurement_breaks(monkeypatch):
    from src.core.pillow_telemetry import begin_pillow_touch_scope, end_pillow_touch_scope, take_pillow_touch_snapshot
    from src.sekai.base import font_metrics

    path = FONTS[0]
    stat = path.stat()
    proxy = font_metrics.NativeFontMetrics(str(path), 20, (stat.st_mtime_ns, stat.st_size))
    expected = ImageFont.truetype(str(path), 20, layout_engine=ImageFont.Layout.BASIC)

    def broken(*args, **kwargs):
        raise RuntimeError("native font service failed")

    monkeypatch.setattr(font_metrics, "_native", lambda: SimpleNamespace(measure_text_batch=broken))
    token = begin_pillow_touch_scope()
    try:
        assert proxy.getbbox("未来 AVATAR") == expected.getbbox("未来 AVATAR")
        assert proxy.getlength("未来 AVATAR") == expected.getlength("未来 AVATAR")
        assert proxy.getmetrics() == expected.getmetrics()
        assert take_pillow_touch_snapshot().counts == {"pillow_text_metric": 3}
    finally:
        end_pillow_touch_scope(token)


def test_basic_engine_is_explicit_and_rejects_unimplemented_effects():
    builder = IRBuilder(
        100,
        50,
        assets_base_dir=str(FONT_DIR),
        font_dir=str(FONT_DIR),
        default_font=FONTS[0].name,
        bold_font=FONTS[0].name,
    )
    builder.text("未来", (0, 0), "default", 20, engine="freetype_basic", letter_spacing=2)
    with pytest.raises(RuntimeError, match="letter spacing"):
        native.render_scene(json.dumps(builder.build()).encode(), {})


def test_basic_baseline_does_not_measure_with_pillow(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("native BASIC text must not measure with Pillow")

    monkeypatch.setattr(IRBuilder, "painter_baseline_y", forbidden)
    builder = IRBuilder(
        100,
        50,
        assets_base_dir=str(FONT_DIR),
        font_dir=str(FONT_DIR),
        default_font=FONTS[0].name,
        bold_font=FONTS[0].name,
    )
    node = builder.text("未来", (0, 0), "default", 20, engine="freetype_basic")
    assert node["baseline"] == "cjk_top"
    native.render_scene(json.dumps(builder.build()).encode(), {})


@pytest.mark.parametrize("pos", [(15.1, 45.1), (15.5, 45.5), (15.9, 45.9), (-1.5, 25.5)])
def test_basic_fractional_origins_match_pillow(pos):
    font = ImageFont.truetype(str(FONTS[0]), 20, layout_engine=ImageFont.Layout.BASIC)
    expected = Image.new("RGBA", (600, 100), "white")
    ImageDraw.Draw(expected).text(pos, "未来 j", font=font, anchor="ls", fill="black")
    assert render_text(FONTS[0], 20, "未来 j", pos=pos).tobytes() == expected.tobytes()


def test_basic_metrics_keep_legacy_kerning_and_fractional_sizes():
    import matplotlib

    path = str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf")
    for size in (12, 20.5, 32):
        font = ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)
        for text in ("AVATAR", "To", "WAVE j", "é e\u0301", "  A  "):
            result = native.measure_text_batch("", path, [(text, size)], engine="freetype_basic")[0]
            assert result["advance"] == font.getlength(text)
            assert result["pillow_bbox"] == font.getbbox(text)


def test_irpainter_keeps_the_resolved_font_path():
    from src.sekai.skia_renderer.ir_painter import IRPainter

    painter = IRPainter(
        (100, 100),
        assets_base_dir=str(FONT_DIR),
        font_dir=str(FONT_DIR),
        default_font=FONTS[0].name,
        bold_font=FONTS[1].name,
        heavy_font=FONTS[2].name,
    )
    font = ImageFont.truetype(str(FONTS[0]), 20, layout_engine=ImageFont.Layout.BASIC)
    painter.text("未来", (0, 0), font)
    scene = painter.builder.build()
    node = scene["root"]["children"][0]
    assert node.get("engine", "skia") == "skia"
    assert node["baseline"] == "alphabetic"
    assert scene["fonts"]["extra"][node["font"]["name"]] == str(FONTS[0])

    # Explicit Pillow-style mask blending must keep its native BASIC coverage.
    painter.text("未来", (0, 0), font, mask_lerp=True)
    node = painter.builder.build()["root"]["children"][-1]
    assert node["engine"] == "freetype_basic"
    assert node["mask_lerp"] is True
    assert node["baseline"] == "cjk_top"


def test_basic_engine_rejects_missing_font_and_oversized_mask(tmp_path):
    with pytest.raises(ValueError, match="without fallback"):
        native.measure_text_batch(str(tmp_path), "missing.otf", [("text", 20)], engine="freetype_basic")
    with pytest.raises(ValueError, match="invalid font size"):
        native.measure_text_batch("", str(FONTS[0]), [("text", float("inf"))], engine="freetype_basic")
    with pytest.raises(RuntimeError, match="pixel limit"):
        render_text(FONTS[0], 2048, "哇" * 4096)


def test_basic_mask_respects_remaining_scene_memory():
    b = IRBuilder(
        100,
        100,
        assets_base_dir=str(FONT_DIR),
        font_dir=str(FONT_DIR),
        default_font=FONTS[0].name,
        bold_font=FONTS[0].name,
    )
    b.text("未来", (0, 0), "default", 32, engine="freetype_basic")
    scene = b.build()
    scene["limits"] = {"max_node_pixels": 100_000, "max_scene_bytes": 40_001}
    with pytest.raises(RuntimeError, match="pixel limit"):
        native.render_scene(json.dumps(scene).encode(), {})
