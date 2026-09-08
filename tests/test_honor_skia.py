from __future__ import annotations

import asyncio
from contextlib import nullcontext

from src.core.image_payload import EncodedImagePayload
import src.sekai.honor.drawer as honor_drawer
from src.sekai.honor.model import HonorRequest
import src.sekai.honor.skia as honor_skia
import src.sekai.honor.widget as honor_widget


def _payload() -> EncodedImagePayload:
    return EncodedImagePayload(
        image_bytes=b"encoded",
        media_type="image/png",
        filename="honor.png",
        image_width=180,
        image_height=96,
        image_mode="RGBA",
        encode_elapsed=0.001,
        native_metrics={"font_fallbacks": 0},
    )


def _request() -> HonorRequest:
    return HonorRequest(honor_type="normal", group_type="event", dt=1_700_000_000_000)


def test_honor_skia_gate_and_import_failure_are_fail_open(monkeypatch) -> None:
    outcomes = []
    monkeypatch.setattr(honor_skia, "_record", lambda outcome, payload=None: outcomes.append(outcome))
    monkeypatch.setattr(honor_skia, "skia_plot_enabled", lambda: False)

    assert asyncio.run(honor_skia.try_render_full_honor_payload(_request())) is None
    assert outcomes == [honor_skia.OUTCOME_DISABLED]

    outcomes.clear()
    monkeypatch.setattr(honor_skia, "skia_plot_enabled", lambda: True)

    def unavailable_renderer():
        raise ImportError("missing native renderer")

    monkeypatch.setattr(honor_skia, "load_native_renderer", unavailable_renderer)

    assert asyncio.run(honor_skia.try_render_full_honor_payload(_request())) is None
    assert outcomes == [honor_skia.OUTCOME_FALLBACK]


def test_honor_skia_returns_cached_payload(monkeypatch) -> None:
    payload = _payload()
    outcomes = []

    monkeypatch.setattr(honor_skia, "skia_plot_enabled", lambda: True)
    monkeypatch.setattr(honor_skia, "load_native_renderer", lambda: object())
    monkeypatch.setattr(honor_drawer, "build_full_honor_cache_key", lambda _request: "badge-key")
    monkeypatch.setattr(honor_skia, "build_request_watermark_text", lambda _request: "watermark")
    monkeypatch.setattr(honor_skia, "get_skia_payload_cached", lambda _key: payload)
    monkeypatch.setattr(honor_skia, "_record", lambda outcome, cached=None: outcomes.append((outcome, cached)))

    assert asyncio.run(honor_skia.try_render_full_honor_payload(_request())) is payload
    assert outcomes == [(honor_skia.OUTCOME_CACHE_HIT, payload)]


def test_honor_skia_renders_shared_subtree_and_caches_payload(monkeypatch) -> None:
    class Native:
        def render_scene(self, scene, mem_images):
            assert b'"width":180' in scene
            assert mem_images == {}
            return {"native": True}

    class Badge:
        size = (180, 80)

        def splice_into(self, builder, mem_images, *, namespace, require_asset_backed):
            assert namespace == "honor.badge"
            assert require_asset_backed is True
            assert mem_images == {}
            builder.spliced = True

    class Builder:
        def __init__(self):
            self.spliced = False
            self.self_images = []
            self.texts = []

        def group(self, *_args, **_kwargs):
            return nullcontext()

        def self_image(self, *args, **kwargs):
            self.self_images.append((args, kwargs))

        def text(self, *args, **kwargs):
            self.texts.append((args, kwargs))

        def build(self):
            assert self.spliced is True
            assert len(self.self_images) == 1
            assert len(self.texts) == 2
            return {"width": 180, "root": {"type": "Group", "children": []}}

    async def load_images(_request):
        return {"honor_img": object()}

    async def immediate_pool(func, *args, **kwargs):
        return func(*args, **kwargs)

    payload = _payload()
    outcomes = []
    cached = []
    builder = Builder()

    monkeypatch.setattr(honor_skia, "skia_plot_enabled", lambda: True)
    monkeypatch.setattr(honor_skia, "load_native_renderer", lambda: Native())
    monkeypatch.setattr(honor_drawer, "build_full_honor_cache_key", lambda _request: "badge-key")
    monkeypatch.setattr(honor_drawer, "load_honor_images", load_images)
    monkeypatch.setattr(honor_widget, "build_honor_badge_canvas", lambda _request, _images: object())
    monkeypatch.setattr(honor_skia, "build_request_watermark_text", lambda _request: "watermark")
    monkeypatch.setattr(honor_skia, "get_skia_payload_cached", lambda _key: None)
    monkeypatch.setattr(honor_skia, "lower_canvas_subtree", lambda *_args, **_kwargs: Badge())
    monkeypatch.setattr(honor_skia, "get_watermark_render_spec", lambda *_args: (12, ["watermark"], 40, 14))
    monkeypatch.setattr(honor_skia, "get_font", lambda *_args: object())
    monkeypatch.setattr(honor_skia, "get_text_size", lambda _font, text: (len(text) * 4, 12))
    monkeypatch.setattr(honor_skia, "_new_builder", lambda *_args, **_kwargs: builder)
    monkeypatch.setattr(honor_skia, "run_in_pool", immediate_pool)
    monkeypatch.setattr(honor_skia, "payload_from_native", lambda result: payload if result else None)
    monkeypatch.setattr(honor_skia, "_record", lambda outcome, result=None: outcomes.append((outcome, result)))
    monkeypatch.setattr(
        honor_skia,
        "put_skia_payload_cache",
        lambda key, result, size: cached.append((key, result, size)),
    )

    result = asyncio.run(honor_skia.try_render_full_honor_payload(_request()))

    assert result is payload
    assert outcomes == [(honor_skia.OUTCOME_SKIA, payload)]
    assert cached == [(cached[0][0], payload, len(payload.image_bytes))]
    assert "badge-key" in cached[0][0]


def test_honor_skia_asset_and_render_failures_are_classified(monkeypatch) -> None:
    outcomes = []

    async def failed_assets(_request):
        raise OSError("unreadable")

    monkeypatch.setattr(honor_skia, "skia_plot_enabled", lambda: True)
    monkeypatch.setattr(honor_skia, "load_native_renderer", lambda: object())
    monkeypatch.setattr(honor_drawer, "build_full_honor_cache_key", lambda _request: "badge-key")
    monkeypatch.setattr(honor_skia, "get_skia_payload_cached", lambda _key: None)
    monkeypatch.setattr(honor_drawer, "load_honor_images", failed_assets)
    monkeypatch.setattr(honor_skia, "_record", lambda outcome, payload=None: outcomes.append(outcome))

    assert asyncio.run(honor_skia.try_render_full_honor_payload(_request())) is None
    assert outcomes == [honor_skia.OUTCOME_FALLBACK]

    outcomes.clear()

    async def load_images(_request):
        return {}

    async def immediate_pool(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(honor_drawer, "load_honor_images", load_images)
    monkeypatch.setattr(honor_widget, "build_honor_badge_canvas", lambda _request, _images: None)
    monkeypatch.setattr(honor_skia, "run_in_pool", immediate_pool)

    assert asyncio.run(honor_skia.try_render_full_honor_payload(_request())) is None
    assert outcomes == [honor_skia.OUTCOME_FALLBACK]

    outcomes.clear()

    async def failed_pool(_func, *_args, **_kwargs):
        raise RuntimeError("native failure")

    monkeypatch.setattr(honor_skia, "run_in_pool", failed_pool)

    assert asyncio.run(honor_skia.try_render_full_honor_payload(_request())) is None
    assert outcomes == [honor_skia.OUTCOME_ERROR]
