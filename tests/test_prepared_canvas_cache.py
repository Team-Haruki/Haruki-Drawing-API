"""Early fragment reuse must have a real miss path and a stable request lease."""

import asyncio
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

from src.sekai.base.canvas_cache import current_canvas_preparation, prepare_cached_canvas
from src.sekai.base.plot import Canvas, CanvasImageBox, FillBg, ImageBox
from src.sekai.base.utils import get_asset_image_ref
from src.sekai.skia_renderer import canvas as renderer, fragment_cache
from src.sekai.skia_renderer.payload_cache import _SkiaPayloadCache


@pytest.fixture
def case(tmp_path, monkeypatch):
    try:
        renderer.load_native_renderer()
    except ImportError:
        pytest.skip("native renderer unavailable")
    pool = _SkiaPayloadCache(8, 1024 * 1024, 60)
    monkeypatch.setattr(fragment_cache, "_cache", pool)
    monkeypatch.setattr(renderer, "ASSETS_BASE_DIR", tmp_path)
    path = tmp_path / "source.png"
    im = Image.new("RGBA", (31, 29))
    im.putdata([(i % 256, i * 7 % 256, i * 13 % 256, i * 11 % 256) for i in range(31 * 29)])
    im.save(path)
    return path, pool


def rgba(payload):
    assert payload is not None
    return Image.open(BytesIO(payload.image_bytes)).convert("RGBA").tobytes()


async def render_child(path, calls, *, key="entry", after=None, size=None, sampling=None, scale=None, fmt="png"):
    async def build_child():
        calls.append(key)
        image = await get_asset_image_ref(path.parent, path.name)
        with Canvas(bg=FillBg((0, 0, 0, 0))).set_size((31, 29)) as child:
            ImageBox(image, size=(31, 29))
        return child

    async def build_page():
        child = await prepare_cached_canvas(key, build_child)
        if after:
            await after(child)
        with Canvas(bg=FillBg((12, 30, 70, 119))).set_size((70, 60)) as page:
            CanvasImageBox(child, size=size, sampling=sampling, cache_key=key)
        return page

    result = await renderer.render_canvas_payload(
        build_page, endpoint="prepared_test", bg_hour=12, scale=scale, export_format=fmt
    )
    assert current_canvas_preparation() is None
    return result


@pytest.mark.parametrize("sampling", [None, "pillow_bicubic", "catmull_rom"])
@pytest.mark.parametrize(("size", "scale"), [(None, None), ((43, 37), 1.5)])
def test_warm_preparation_skips_factory_and_matches_uncached(case, sampling, size, scale):
    path, pool = case

    async def go():
        calls = []
        expected = rgba(await render_child(path, calls, key=None, size=size, sampling=sampling, scale=scale))
        first = rgba(await render_child(path, calls, size=size, sampling=sampling, scale=scale))
        second = rgba(await render_child(path, calls, size=size, sampling=sampling, scale=scale))
        assert first == second == expected
        assert calls == [None, "entry"]
        assert pool.stats()["entries"] == 1

    asyncio.run(go())


@pytest.mark.parametrize("action", ["clear", "evict", "expire"])
def test_lease_survives_concurrent_eviction_but_next_request_rebuilds(case, action):
    path, pool = case

    async def go():
        calls = []
        expected = rgba(await render_child(path, calls))

        async def evict(_child):
            def change_cache():
                if action == "clear":
                    pool.clear()
                elif action == "evict":
                    pool._max_size = 1
                    pool.set("unrelated", object(), 1)
                else:
                    with pool._lock:
                        for key, (value, weight, _) in list(pool._cache.items()):
                            pool._cache[key] = (value, weight, 0)

            await asyncio.to_thread(change_cache)

        assert rgba(await render_child(path, calls, after=evict)) == expected
        assert calls == ["entry"]
        assert rgba(await render_child(path, calls)) == expected
        assert calls == ["entry", "entry"]

    asyncio.run(go())


def test_replacement_invalidates_next_preparation(case):
    path, _pool = case

    async def go():
        calls = []
        before = rgba(await render_child(path, calls))
        Image.new("RGBA", (31, 29), "blue").save(path)
        after = rgba(await render_child(path, calls))
        assert before != after
        assert calls == ["entry", "entry"]
        assert rgba(await render_child(path, calls)) == after
        assert len(calls) == 2

    asyncio.run(go())


def test_disabled_pool_and_reference_builder_always_build_real_tree(case):
    path, pool = case

    async def go():
        calls = []
        expected = rgba(await render_child(path, calls))
        pool._max_bytes = 0
        for _ in range(2):
            assert rgba(await render_child(path, calls)) == expected
        assert len(calls) == 3

        async def build():
            return Canvas(w=3, h=2, bg=FillBg((255, 0, 0, 255)))

        reference = await prepare_cached_canvas("entry", build)
        assert not hasattr(reference, "_native_prepared_canvas")
        assert (await reference.get_img()).size == (3, 2)

    asyncio.run(go())


def test_renderer_options_and_font_replacement_invalidate(case, monkeypatch, tmp_path):
    path, _pool = case
    font_path = next(
        path
        for suffix in (".otf", ".ttf", ".ttc", "")
        if (path := Path(renderer.FONT_DIR) / (renderer.DEFAULT_FONT + suffix)).is_file()
    )
    replacement = tmp_path / "replacement.otf"
    replacement.write_bytes(font_path.read_bytes())
    monkeypatch.setattr(renderer, "DEFAULT_EMOJI_FONT", str(replacement))

    async def go():
        calls = []
        assert await render_child(path, calls) is not None
        assert await render_child(path, calls) is not None
        assert len(calls) == 1
        replacement.touch()
        assert await render_child(path, calls) is not None
        assert len(calls) == 2
        assert await render_child(path, calls, fmt="jpg") is not None
        assert len(calls) == 3

    asyncio.run(go())


def test_factory_failure_resets_context(case):
    async def fail():
        assert current_canvas_preparation() is not None
        raise ValueError("factory failed")

    assert asyncio.run(renderer.render_canvas_payload(fail, endpoint="prepared_failure")) is None
    assert current_canvas_preparation() is None


def test_event_phase_changes_rebuild_but_footer_remains_outside_fragment(case, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from src.sekai.event import drawer

    now = datetime(2026, 9, 7, tzinfo=UTC)
    event = SimpleNamespace(start_at=now, end_at=now + timedelta(days=1))
    calls = []

    async def load(_):
        calls.append("load")
        return {}

    def build(_event, _assets, phase, *_styles):
        return Canvas(w=10, h=10, bg=FillBg((255, 0, 0, 255) if phase == "current" else (0, 0, 255, 255)))

    monkeypatch.setattr(drawer, "_preload_event_entry_assets", load)
    monkeypatch.setattr(drawer, "_build_event_list_entry_canvas", build)
    monkeypatch.setattr(drawer, "_build_event_list_entry_cache_key", lambda _, phase: phase)

    async def render(time, footer_color):
        async def page():
            child, key = await drawer._get_event_list_entry_canvas(event, time, None, None)
            with Canvas(
                w=20, h=20, bg=FillBg({"white": (255, 255, 255, 255), "green": (0, 255, 0, 255)}[footer_color])
            ) as result:
                CanvasImageBox(child, cache_key=key)
            return result

        return rgba(await renderer.render_canvas_payload(page, bg_hour=12))

    async def go():
        a = await render(now, "white")
        b = await render(now, "green")
        assert a != b
        assert len(calls) == 1
        c = await render(now + timedelta(days=2), "green")
        assert c != b
        assert len(calls) == 2
        assert await render(now, "white") == a
        assert len(calls) == 2

    asyncio.run(go())


def test_missing_asset_arrival_changes_preparation_key(case):
    import json

    from src.sekai.base.utils import get_image_asset_signature

    path, _pool = case
    path.unlink()

    async def go():
        calls = []
        key = json.dumps(get_image_asset_signature(path.parent, path.name), sort_keys=True)
        first = rgba(await render_child(path, calls, key=key))
        Image.new("RGBA", (31, 29), (255, 0, 0, 255)).save(path)
        key = json.dumps(get_image_asset_signature(path.parent, path.name), sort_keys=True)
        second = rgba(await render_child(path, calls, key=key))
        assert first != second
        assert rgba(await render_child(path, calls, key=key)) == second
        assert len(calls) == 2

    asyncio.run(go())


def test_concurrent_requests_keep_distinct_preparation_contexts(case):
    path, _pool = case

    async def go():
        png_calls, jpg_calls = [], []
        png, jpg = await asyncio.gather(render_child(path, png_calls), render_child(path, jpg_calls, fmt="jpg"))
        assert png.media_type == "image/png"
        assert jpg.media_type == "image/jpeg"
        await asyncio.gather(render_child(path, png_calls), render_child(path, jpg_calls, fmt="jpg"))
        assert len(png_calls) == len(jpg_calls) == 1

    asyncio.run(go())
