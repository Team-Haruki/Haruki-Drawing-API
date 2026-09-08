"""Native fragment reuse must preserve pixels and follow changing dependencies."""

import asyncio
from io import BytesIO

from PIL import Image
import pytest

from src.sekai.base.plot import Canvas, CanvasImageBox, FillBg, ImageBox
from src.sekai.base.utils import get_asset_image_ref
from src.sekai.skia_renderer import fragment_cache
from src.sekai.skia_renderer.canvas import build_canvas_ir, load_native_renderer
from src.sekai.skia_renderer.payload_cache import _SkiaPayloadCache


@pytest.fixture
def pool(monkeypatch):
    cache = _SkiaPayloadCache(20, 4 * 1024 * 1024, 60)
    monkeypatch.setattr(fragment_cache, "_cache", cache)
    return cache


@pytest.fixture
def native():
    try:
        return load_native_renderer()
    except ImportError:
        pytest.skip("current native renderer required")


def _canvas(ref, key="test-fragment", sampling="catmull_rom"):
    with Canvas(bg=FillBg((0, 0, 0, 0))).set_size((27, 19)) as child:
        ImageBox(ref, size=(27, 19))
    with Canvas(bg=FillBg((20, 40, 60, 127))).set_size((60, 50)) as parent:
        CanvasImageBox(child, size=(43, 37), sampling=sampling, cache_key=key)
    return parent


@pytest.mark.parametrize("sampling", ["catmull_rom", "pillow_bicubic"])
def test_cached_fragment_matches_uncached_isolated_raster(native, pool, tmp_path, sampling):
    path = tmp_path / "alpha.png"
    image = Image.new("RGBA", (91, 57))
    image.putdata([(i % 256, i * 7 % 256, i * 13 % 256, i * 11 % 256) for i in range(91 * 57)])
    image.save(path)
    ref = asyncio.run(get_asset_image_ref(tmp_path, path.name))
    import json

    def render(key):
        builder, memory = build_canvas_ir(_canvas(ref, key, sampling), assets_base_dir=str(tmp_path))
        result = native.render_scene(json.dumps(builder.build()).encode(), memory)
        return Image.open(BytesIO(result["image_bytes"])).convert("RGBA").tobytes()

    expected = render(None)
    assert render("cached") == expected
    assert render("cached") == expected
    assert pool.stats()["hits"] > 0


def test_asset_replacement_invalidates_early_fragment_lookup(native, pool, tmp_path):
    import json

    path = tmp_path / "replace.png"

    def render(color):
        Image.new("RGBA", (27, 19), color).save(path)
        ref = asyncio.run(get_asset_image_ref(tmp_path, path.name))
        builder, memory = build_canvas_ir(_canvas(ref), assets_base_dir=str(tmp_path))
        result = native.render_scene(json.dumps(builder.build()).encode(), memory)
        return Image.open(BytesIO(result["image_bytes"])).convert("RGBA").tobytes()

    first = render("red")
    second = render("blue")
    assert first != second
    assert pool.stats()["sets"] == 2


def test_font_arrival_and_removal_invalidate_cached_dependency(pool, tmp_path):
    font = tmp_path / "font.ttf"
    signature = fragment_cache.get_image_asset_signature(tmp_path, font.name)
    from src.sekai.base.image_source import EncodedImageRef

    image = EncodedImageRef(b"test", (1, 1), "RGBA")
    value = fragment_cache._Fragment(image, ((str(tmp_path), font.name, signature),))
    pool.set("key", value, 100)
    assert fragment_cache.get_native_fragment_cached("key") is image
    font.write_bytes(b"new font")
    assert fragment_cache.get_native_fragment_cached("key") is None
    pool.set(
        "key",
        fragment_cache._Fragment(
            image, ((str(tmp_path), font.name, fragment_cache.get_image_asset_signature(tmp_path, font.name)),)
        ),
        100,
    )
    assert fragment_cache.get_native_fragment_cached("key") is image
    font.unlink()
    assert fragment_cache.get_native_fragment_cached("key") is None


def test_native_fragment_cache_follows_runtime_clear_and_stats(pool):
    from src.sekai.base.utils import clear_runtime_memory_caches, get_runtime_cache_stats

    pool.set("key", fragment_cache._Fragment(None, ()), 100)
    assert get_runtime_cache_stats()["native_fragment_cache"]["entries"] == 1
    clear_runtime_memory_caches()
    assert get_runtime_cache_stats()["native_fragment_cache"]["entries"] == 0


def test_fragment_lookup_includes_layout_context_and_source_code(monkeypatch):
    key = fragment_cache.canvas_fragment_lookup_key
    monkeypatch.setattr(fragment_cache, "renderer_code_fingerprint", lambda: "first")
    first = key("request", (12, 34), {"bg_hour": 12})
    assert key("different-request", (12, 34), {"bg_hour": 12}) != first
    assert key("request", (13, 34), {"bg_hour": 12}) != first
    assert key("request", (12, 34), {"bg_hour": 13}) != first
    monkeypatch.setattr(fragment_cache, "renderer_code_fingerprint", lambda: "second")
    assert key("request", (12, 34), {"bg_hour": 12}) != first


def test_fragment_clock_only_invalidates_clock_dependent_rasters(pool):
    from src.sekai.base.image_source import EncodedImageRef

    image = EncodedImageRef(b"test", (1, 1), "RGBA")
    pool.set("static", fragment_cache._Fragment(image, ()), 4)
    pool.set("triangles", fragment_cache._Fragment(image, (), 12.0), 4)
    assert fragment_cache.get_native_fragment_cached("static", bg_hour=12.1) is image
    assert fragment_cache.get_native_fragment_cached("triangles", bg_hour=12.0) is image
    assert fragment_cache.get_native_fragment_cached("triangles", bg_hour=12.1) is None


def test_disabled_cache_never_renders_fragment(pool, monkeypatch):
    from types import SimpleNamespace

    pool._max_size = 0
    monkeypatch.setattr(fragment_cache, "fragment_scene_and_key", lambda *a, **k: pytest.fail("cache disabled"))
    assert fragment_cache.render_cached_native_fragment(SimpleNamespace(asset_backed=True), "key") is None


def test_placeholder_fragment_reuses_pixels_and_asset_arrival_invalidates(native, pool, tmp_path, monkeypatch):
    import json

    from src.sekai.base.utils import build_rendered_image_cache_key, collect_asset_signatures
    from src.sekai.skia_renderer import placeholder

    path = tmp_path / "late.png"
    original = placeholder.render_placeholder
    calls = []

    def traced(source):
        calls.append(source.variant)
        return original(source)

    monkeypatch.setattr(placeholder, "render_placeholder", traced)

    def render():
        ref = asyncio.run(get_asset_image_ref(tmp_path, path.name))
        key = build_rendered_image_cache_key(
            "late", path.name, asset_signatures=collect_asset_signatures(tmp_path, path.name)
        )
        builder, memory = build_canvas_ir(_canvas(ref, key), assets_base_dir=str(tmp_path))
        return native.render_scene(json.dumps(builder.build()).encode(), memory)["image_bytes"]

    first = render()
    assert render() == first
    assert len(calls) == 1
    Image.new("RGBA", (27, 19), "red").save(path)
    assert render() != first
    assert len(calls) == 1


def test_arbitrary_encoded_memory_does_not_enter_fragment_cache(native, pool):
    from src.sekai.base.image_source import EncodedImageRef

    buffer = BytesIO()
    Image.new("RGBA", (3, 3), "red").save(buffer, "PNG")
    build_canvas_ir(_canvas(EncodedImageRef(buffer.getvalue(), (3, 3), "RGBA")))
    assert pool.stats()["entries"] == 0


def test_asset_alpha_trim_cache_keys_destination_size(native, pool, tmp_path):
    import json

    from src.sekai.base.image_info import probe_alpha_bounds
    from src.sekai.base.plot import AlphaTrimImageBox

    path = tmp_path / "trim.png"
    Image.new("RGBA", (80, 90), (120, 30, 50, 130)).save(path)
    ref = asyncio.run(get_asset_image_ref(tmp_path, path.name))

    def render(size, cached):
        with Canvas(bg=FillBg((50, 60, 80, 180))).set_size((100, 100)) as canvas:
            AlphaTrimImageBox(ref, probe_alpha_bounds(ref), 36, size=size, **({} if cached else {"cache_key": None}))
        builder, memory = build_canvas_ir(canvas, assets_base_dir=str(tmp_path))
        payload = native.render_scene(json.dumps(builder.build()).encode(), memory)
        return Image.open(BytesIO(payload["image_bytes"])).convert("RGBA").tobytes()

    for size in [(23, 31), (47, 39), (23, 31)]:
        assert render(size, True) == render(size, False)
    assert pool.stats()["entries"] == 2


def test_absolute_and_placeholder_font_dependencies_are_validated(pool, tmp_path):
    from types import SimpleNamespace

    from src.sekai.base.image_source import EncodedImageRef
    from src.sekai.skia_renderer.placeholder import NativePlaceholderBytes

    font = tmp_path / "outside.ttf"
    generated_font = tmp_path / "placeholder.ttf"
    font.write_bytes(b"font")
    generated_font.write_bytes(b"generated font")
    dependency = (
        str(tmp_path),
        generated_font.name,
        fragment_cache.get_image_asset_signature(tmp_path, generated_font.name),
    )
    subtree = SimpleNamespace(
        size=(1, 1),
        assets_base_dir=str(tmp_path),
        fonts={"dir": str(tmp_path / "different-root"), "default": str(font)},
        nodes=(),
        mem_images={"generated": NativePlaceholderBytes(b"png", (dependency,))},
    )
    _, _, dependencies = fragment_cache.fragment_scene_and_key(subtree, "test")
    image = EncodedImageRef(b"test", (1, 1), "RGBA")
    for changed in (font, generated_font):
        pool.set("key", fragment_cache._Fragment(image, dependencies), 100)
        assert fragment_cache.get_native_fragment_cached("key") is image
        saved = changed.read_bytes()
        changed.write_bytes(saved + b"replacement")
        assert fragment_cache.get_native_fragment_cached("key") is None
        changed.write_bytes(saved)
        _, _, dependencies = fragment_cache.fragment_scene_and_key(
            SimpleNamespace(**{**subtree.__dict__, "mem_images": {}}), "test"
        )
        # Re-capture the generated dependency for the next independent replacement.
        dependencies += (
            (
                str(tmp_path),
                generated_font.name,
                fragment_cache.get_image_asset_signature(tmp_path, generated_font.name),
            ),
        )


@pytest.mark.parametrize("scale", [None, 1.5, 2.0])
@pytest.mark.parametrize("sampling", [None, "pillow_bicubic"])
def test_one_to_one_cached_fragment_keeps_device_sampling(native, pool, tmp_path, scale, sampling):
    import json

    path = tmp_path / "scaled.png"
    image = Image.new("RGBA", (27, 19))
    image.putdata([(i % 256, i * 7 % 256, i * 13 % 256, i * 11 % 256) for i in range(27 * 19)])
    image.save(path)
    ref = asyncio.run(get_asset_image_ref(tmp_path, path.name))

    def render(cached):
        with Canvas(bg=FillBg((0, 0, 0, 0))).set_size((27, 19)) as child:
            ImageBox(ref, size=(27, 19))
        with Canvas(bg=FillBg((20, 40, 60, 127))).set_size((60, 50)) as parent:
            CanvasImageBox(child, cache_key="one-to-one" if cached else None, sampling=sampling)
        builder, memory = build_canvas_ir(parent, assets_base_dir=str(tmp_path))
        scene = builder.build()
        if scale is not None:
            scene["scale"] = scale
        payload = native.render_scene(json.dumps(scene).encode(), memory)
        return Image.open(BytesIO(payload["image_bytes"])).convert("RGBA").tobytes()

    assert render(True) == render(False)


def test_decoded_fragment_is_immutable_raw_and_reused(native, pool, tmp_path, monkeypatch):
    from src.sekai.base.image_source import NativeRasterImageRef

    if not hasattr(native, "decode_fragment_rgba"):
        pytest.skip("fragment decoder unavailable")
    path = tmp_path / "decoded.png"
    Image.new("RGBA", (27, 19), (121, 43, 87, 93)).save(path)
    ref = asyncio.run(get_asset_image_ref(tmp_path, path.name))
    canvas = _canvas(ref)
    _, first = build_canvas_ir(canvas, assets_base_dir=str(tmp_path))
    raw = next(value for value in first.values() if isinstance(value, tuple))
    assert raw[3:5] == ("rgba8888", "premul")
    assert isinstance(raw[5], bytes)
    monkeypatch.setattr(native, "decode_fragment_rgba", lambda *_: pytest.fail("warm fragment decoded again"))
    _, second = build_canvas_ir(_canvas(ref), assets_base_dir=str(tmp_path))
    assert next(value for value in second.values() if isinstance(value, tuple))[5] is raw[5]
    assert NativeRasterImageRef(raw[5], (27, 19)).mode == "RGBA"
    assert pool.stats()["bytes"] >= len(raw[5])


def test_two_stage_resize_cache_keeps_pixels_and_tracks_destination(native, pool, tmp_path):
    import json

    from src.sekai.base.plot import PreResizedImageBox

    path = tmp_path / "two-stage.png"
    image = Image.new("RGBA", (83, 69))
    image.putdata([(i % 256, i * 7 % 256, i * 13 % 256, i * 11 % 256) for i in range(83 * 69)])
    image.save(path)
    ref = asyncio.run(get_asset_image_ref(tmp_path, path.name))

    def render(size, cached):
        with Canvas(bg=FillBg((21, 39, 67, 127))).set_size((70, 60)) as parent:
            kwargs = {} if cached else {"cache_key": None}
            PreResizedImageBox(ref, pre_size=(40, 40), size=size, **kwargs).set_padding(4)
        builder, memory = build_canvas_ir(parent, assets_base_dir=str(tmp_path))
        result = native.render_scene(json.dumps(builder.build()).encode(), memory)
        return Image.open(BytesIO(result["image_bytes"])).convert("RGBA").tobytes()

    for size in [(40, 40), (49, 43)]:
        expected = render(size, False)
        assert render(size, True) == expected
        assert render(size, True) == expected
    assert pool.stats()["sets"] == 2


def test_embedded_text_keeps_basic_pixels_with_cache_on_and_off(native, pool, real_fonts):
    import json

    from src.sekai.base.plot import TextBox, TextStyle
    from src.settings import DEFAULT_FONT

    def child():
        with Canvas(w=170, h=55, bg=FillBg((255, 255, 255, 255))).set_padding(4) as page:
            TextBox("瑞希 779", TextStyle(font=DEFAULT_FONT, size=24))
        return page

    expected = asyncio.run(child().get_img()).convert("RGBA").tobytes()

    def render(key):
        with Canvas(w=170, h=55).set_padding(0) as page:
            CanvasImageBox(child(), size=(170, 55), cache_key=key)
        builder, memory = build_canvas_ir(page)
        result = native.render_scene(json.dumps(builder.build()).encode(), memory)
        return Image.open(BytesIO(result["image_bytes"])).convert("RGBA").tobytes()

    assert render(None) == expected
    assert render("basic-child") == expected
    assert render("basic-child") == expected
    assert pool.stats()["hits"] > 0
