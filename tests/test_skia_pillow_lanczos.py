"""Native integration tests for the explicit Pillow-compatible Lanczos IR sampling."""

from __future__ import annotations

from io import BytesIO
import json

from PIL import Image
import pytest

from src.sekai.skia_renderer.canvas import REQUIRED_NATIVE_IR_CAPABILITY
from src.sekai.skia_renderer.ir_builder import IRBuilder

try:
    import haruki_skia_renderer as _native
except ImportError:  # pragma: no cover - quick-check's non-native job
    _native = None


pytestmark = pytest.mark.skipif(
    _native is None or getattr(_native, "IR_CAPABILITY", 0) < REQUIRED_NATIVE_IR_CAPABILITY,
    reason="capability-15 native renderer is required",
)


def _builder(width: int, height: int, assets_base_dir) -> IRBuilder:
    return IRBuilder(
        width,
        height,
        assets_base_dir=str(assets_base_dir),
        font_dir=str(assets_base_dir),
        default_font="unused.ttf",
        bold_font="unused.ttf",
        export_format="png",
    )


def _render(builder: IRBuilder) -> Image.Image:
    result = _native.render_scene(json.dumps(builder.build()).encode(), {})
    return Image.open(BytesIO(result["image_bytes"])).convert("RGBA")


def _opaque_pattern(size: tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size)
    image.putdata(
        [
            (
                (x * 37 + y * 11) % 256,
                (x * 7 + y * 43) % 256,
                (x * 19 + y * 29) % 256,
                255,
            )
            for y in range(height)
            for x in range(width)
        ]
    )
    return image


def test_native_pillow_lanczos_stretch_and_cover_match_pillow(tmp_path):
    source = _opaque_pattern((19, 13))
    source.save(tmp_path / "source.png")

    stretch = _builder(11, 7, tmp_path)
    stretch.image("source.png", (0, 0), (11, 7), sampling="pillow_lanczos")
    assert _render(stretch).tobytes() == source.resize((11, 7), Image.Resampling.LANCZOS).tobytes()

    cover = _builder(9, 5, tmp_path)
    cover.image(
        "source.png",
        (-2, -1),
        (13, 7),
        fit="cover",
        sampling="pillow_lanczos",
        blend="src",
    )
    scale = max(13 / source.width, 7 / source.height)
    resized = source.resize(
        (round(source.width * scale), round(source.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = round((resized.width - 13) * 0.5)
    top = round((resized.height - 7) * 0.5)
    fitted = resized.crop((left, top, left + 13, top + 7))
    expected = fitted.crop((2, 1, 11, 6))
    assert _render(cover).tobytes() == expected.tobytes()


def test_native_pillow_lanczos_unity_subscene_matches_sequential_pillow_resizes(tmp_path):
    source = _opaque_pattern((4, 4))
    source.save(tmp_path / "source.png")

    builder = _builder(4, 3, tmp_path)
    with builder.unity_subscene(
        size=(4, 4),
        anchor=(2, 1.5),
        object_scale=(1.75, 1.25),
        post_scale=(0.5714286, 0.6),
        rotation=0,
        sampling="pillow_lanczos",
    ):
        builder.image(
            "source.png",
            (0, 0),
            (4, 4),
            sampling="pillow_lanczos",
            blend="src",
        )

    expected = source.resize((7, 5), Image.Resampling.LANCZOS).resize(
        (4, 3),
        Image.Resampling.LANCZOS,
    )
    assert _render(builder).tobytes() == expected.tobytes()


def test_native_pillow_lanczos_errors_instead_of_using_another_sampler(tmp_path):
    builder = _builder(4, 4, tmp_path)
    builder.image("missing.png", (0, 0), (4, 4), sampling="pillow_lanczos")
    with pytest.raises(RuntimeError, match="pillow_lanczos Image asset load failed"):
        _render(builder)

    rotated = _builder(4, 4, tmp_path)
    with rotated.unity_subscene(
        size=(4, 4),
        anchor=(2, 2),
        object_scale=(1, 1),
        post_scale=(1, 1),
        rotation=1,
        sampling="pillow_lanczos",
    ):
        pass
    with pytest.raises(RuntimeError, match="requires zero rotation"):
        _render(rotated)
