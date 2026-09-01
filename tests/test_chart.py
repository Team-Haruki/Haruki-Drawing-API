import asyncio
import struct

import pytest

from src.core.image_payload import EncodedImagePayload
from src.sekai.chart import drawer
from src.sekai.chart.drawer import load_score
from src.sekai.chart.model import GenerateMusicChartRequest


def test_load_score_accepts_custom_chart_json():
    request = GenerateMusicChartRequest(
        music_id="custom-score-1",
        title="Custom",
        artist="Tester",
        difficulty="master",
        play_level=31,
        jacket_path="static_images/chart_asset/sample.png",
        note_host="static_images/chart_asset/notes",
        chart_json={
            "MusicScoreEventDataList": [
                {"id": 1, "ticks": 0, "eventType": 0, "changeValue": 120},
            ],
            "NoteList": [],
        },
    )

    score = load_score(request)

    assert score.event_count() == 1
    assert score.note_count() == 0


def test_render_chart_mem_image_prefers_zero_copy_raster(monkeypatch):
    class Raster:
        width = 5
        height = 3
        row_bytes = 20
        color_type = "bgra8888"
        alpha_type = "premul"

    raster = Raster()

    class Drawing:
        def raster(self, score):
            assert score == "score"
            return raster

        def png(self, score):
            raise AssertionError("PNG transport should not be used")

    monkeypatch.setattr(drawer, "_prepare_chart_render", lambda request: (Drawing(), "score"))

    mem_image, width, height, transport = drawer.render_chart_mem_image(object())

    assert mem_image == (5, 3, 20, "bgra8888", "premul", raster)
    assert (width, height, transport) == (5, 3, "raw-n32")


def test_render_chart_mem_image_falls_back_to_png(monkeypatch):
    png = b"\x89PNG\r\n\x1a\n" + (b"\0" * 8) + struct.pack(">II", 7, 4)

    class Drawing:
        def raster(self, score):
            raise AssertionError("old native capability should keep PNG transport")

        def png(self, score):
            assert score == "score"
            return png

    monkeypatch.setattr(drawer, "_prepare_chart_render", lambda request: (Drawing(), "score"))

    mem_image, width, height, transport = drawer.render_chart_mem_image(object(), allow_raster=False)

    assert mem_image is png
    assert (width, height, transport) == (7, 4, "png")


def _chart_request() -> GenerateMusicChartRequest:
    return GenerateMusicChartRequest(
        music_id="custom-score-1",
        title="Custom",
        artist="Tester",
        difficulty="master",
        play_level=31,
        jacket_path="jacket.png",
        note_host="notes",
        chart_json={"MusicScoreEventDataList": [], "NoteList": []},
        dt=1_700_000_000_000,
    )


def _chart_payload() -> EncodedImagePayload:
    return EncodedImagePayload(
        image_bytes=b"encoded",
        media_type="image/png",
        filename="chart.png",
        image_width=80,
        image_height=64,
        image_mode="RGBA",
        encode_elapsed=0.001,
        native_metrics={"font_fallbacks": 0},
    )


def test_chart_skia_gate_and_import_failure_are_fail_open(monkeypatch):
    outcomes = []
    monkeypatch.setattr(drawer, "_record", lambda outcome, payload=None: outcomes.append(outcome))
    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: False)

    assert asyncio.run(drawer.try_render_music_chart_payload(_chart_request())) is None
    assert outcomes == [drawer.OUTCOME_DISABLED]

    outcomes.clear()
    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: True)

    def unavailable_renderer():
        raise ImportError("missing native renderer")

    monkeypatch.setattr(drawer, "load_native_renderer", unavailable_renderer)

    assert asyncio.run(drawer.try_render_music_chart_payload(_chart_request())) is None
    assert outcomes == [drawer.OUTCOME_FALLBACK]


@pytest.mark.parametrize("raw_capability", [0, 1])
def test_chart_skia_builds_watermarked_scene_for_supported_transports(monkeypatch, raw_capability):
    observed = {}

    class Native:
        RAW_BUFFER_CAPABILITY = raw_capability

        def render_scene(self, scene, mem_images):
            observed["scene"] = scene
            observed["mem_images"] = mem_images
            return {"native": True}

    async def immediate_pool(func, *args, **kwargs):
        return func(*args, **kwargs)

    def fake_chart_image(_request, *, allow_raster):
        observed["allow_raster"] = allow_raster
        transport = "raw-n32" if allow_raster else "png"
        return b"chart", 80, 48, transport

    payload = _chart_payload()
    outcomes = []
    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: True)
    monkeypatch.setattr(drawer, "load_native_renderer", lambda: Native())
    monkeypatch.setattr(drawer, "render_chart_mem_image", fake_chart_image)
    monkeypatch.setattr(drawer, "run_in_pool", immediate_pool)
    monkeypatch.setattr(drawer, "payload_from_native", lambda result: payload if result else None)
    monkeypatch.setattr(drawer, "get_watermark_render_spec", lambda *_args: (12, ["watermark"], 40, 14))
    monkeypatch.setattr(drawer, "get_font", lambda *_args: object())
    monkeypatch.setattr(drawer, "get_text_size", lambda _font, text: (len(text) * 4, 12))
    monkeypatch.setattr(drawer, "_record", lambda outcome, result=None: outcomes.append((outcome, result)))

    result = asyncio.run(drawer.try_render_music_chart_payload(_chart_request()))

    assert result is payload
    assert observed["allow_raster"] is bool(raw_capability)
    assert observed["mem_images"] == {"chart": b"chart"}
    assert b'"path":"mem:chart"' in observed["scene"]
    assert outcomes == [(drawer.OUTCOME_SKIA, payload)]


def test_chart_skia_render_failure_is_classified(monkeypatch):
    outcomes = []

    class Native:
        RAW_BUFFER_CAPABILITY = 1

    async def failed_pool(_func, *_args, **_kwargs):
        raise RuntimeError("native failure")

    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: True)
    monkeypatch.setattr(drawer, "load_native_renderer", lambda: Native())
    monkeypatch.setattr(drawer, "run_in_pool", failed_pool)
    monkeypatch.setattr(drawer, "_record", lambda outcome, payload=None: outcomes.append(outcome))

    assert asyncio.run(drawer.try_render_music_chart_payload(_chart_request())) is None
    assert outcomes == [drawer.OUTCOME_ERROR]
