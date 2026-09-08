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
    reason="the current native renderer capability is required",
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


@pytest.mark.parametrize(("pre_size", "final_size"), [((92, 92), (84, 84)), ((40, 40), (32, 32))])
def test_native_two_stage_bilinear_bicubic_preserves_intermediate_raster(tmp_path, pre_size, final_size):
    source = _opaque_pattern((137, 121))
    source.save(tmp_path / "source.png")
    builder = _builder(*final_size, tmp_path)
    with builder.raster_subscene(natural_size=pre_size, pos=(0, 0), dst_size=final_size, sampling="pillow_bicubic"):
        builder.image("source.png", (0, 0), pre_size, sampling="pillow_bilinear", blend="src")
    expected = source.resize(pre_size, Image.Resampling.BILINEAR).resize(final_size, Image.Resampling.BICUBIC)
    assert _render(builder).tobytes() == expected.tobytes()
    assert expected.tobytes() != source.resize(final_size, Image.Resampling.BICUBIC).tobytes()


def test_bicubic_subscene_checks_resize_memory_before_allocating(tmp_path):
    builder = _builder(4, 4, tmp_path)
    with builder.raster_subscene(natural_size=(4, 4), pos=(0, 0), dst_size=(128, 128), sampling="pillow_bicubic"):
        builder.rect((0, 0), (4, 4), fill=(1, 2, 3, 255))
    scene = builder.build()
    scene["limits"] = {"max_scene_bytes": 4096}
    with pytest.raises(RuntimeError, match=r"bicubic RasterSubscene.*bytes"):
        _native.render_scene(json.dumps(scene).encode(), {})


def test_two_stage_resize_preserves_translucent_edges(tmp_path):
    import numpy as np

    source = _opaque_pattern((73, 61))
    alpha = Image.new("L", source.size)
    alpha.putdata([(x * 19 + y * 31) % 256 for y in range(source.height) for x in range(source.width)])
    source.putalpha(alpha)
    source.save(tmp_path / "source.png")
    builder = _builder(32, 32, tmp_path)
    builder.rect((0, 0), (32, 32), fill=(255, 255, 255, 255))
    with builder.raster_subscene(natural_size=(40, 40), pos=(0, 0), dst_size=(32, 32), sampling="pillow_bicubic"):
        builder.image("source.png", (0, 0), (40, 40), sampling="pillow_bilinear", blend="src")
    expected = Image.new("RGBA", (32, 32), "white")
    expected.alpha_composite(
        source.resize((40, 40), Image.Resampling.BILINEAR).resize((32, 32), Image.Resampling.BICUBIC)
    )
    diff = np.abs(np.asarray(_render(builder)).astype(int) - np.asarray(expected).astype(int))
    assert diff.max() <= 2


@pytest.mark.parametrize(("source_size", "target_size"), [((19, 13), (11, 7)), ((8, 5), (31, 19)), ((2, 301), (2, 7))])
def test_native_bicubic_matches_pillow_for_shrink_enlarge_and_tall_inputs(tmp_path, source_size, target_size):
    source = _opaque_pattern(source_size)
    source.save(tmp_path / "source.png")
    builder = _builder(*target_size, tmp_path)
    builder.image("source.png", (0, 0), target_size, sampling="pillow_bicubic")
    assert _render(builder).tobytes() == source.resize(target_size, Image.Resampling.BICUBIC).tobytes()


def test_native_bicubic_transparent_pixels_match_pillow(tmp_path):
    import numpy as np

    rng = np.random.default_rng(947)
    pixels = rng.integers(0, 256, (23, 37, 4), dtype=np.uint8)
    source = Image.fromarray(pixels)
    source.save(tmp_path / "source.png")
    builder = _builder(13, 9, tmp_path)
    builder.image("source.png", (0, 0), (13, 9), sampling="pillow_bicubic", blend="src")
    actual = np.asarray(_render(builder)).astype(int)
    expected = np.asarray(source.resize((13, 9), Image.Resampling.BICUBIC)).astype(int)
    # Skia's premultiplied destination adds one quantization at final placement.
    assert np.abs(actual[:, :, :3] - expected[:, :, :3]).max() <= 2
    assert np.array_equal(actual[:, :, 3], expected[:, :, 3])


def test_native_bicubic_keeps_strict_memory_limit(tmp_path):
    source = _opaque_pattern((19, 13))
    source.save(tmp_path / "source.png")
    builder = _builder(11, 7, tmp_path)
    builder.image("source.png", (0, 0), (11, 7), sampling="pillow_bicubic")
    scene = builder.build()
    scene["limits"] = {"max_node_pixels": 1000, "max_scene_bytes": 1500}
    with pytest.raises(RuntimeError, match="limit"):
        _native.render_scene(json.dumps(scene).encode(), {})
