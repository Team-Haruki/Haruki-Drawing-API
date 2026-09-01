import asyncio
from io import BytesIO
import struct

from PIL import Image
import pytest

from src.core.image_payload import EncodedImagePayload
from src.sekai.chart import drawer
from src.sekai.chart.drawer import load_score
from src.sekai.chart.model import GenerateMusicChartRequest


async def _async_value(value):
    return value


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


def test_chart_font_score_and_prepare_helpers_cover_file_and_fallback_inputs(tmp_path, monkeypatch):
    class FakeFontDir:
        def __truediv__(self, filename):
            return tmp_path / filename

        def __str__(self):
            return str(tmp_path)

    monkeypatch.setattr(drawer, "FONT_DIR", FakeFontDir())
    assert drawer.chart_font_kwargs() == {"font_dirs": [str(tmp_path)]}
    (tmp_path / drawer.CHART_FONT_FILENAMES[0]).touch()
    assert drawer.chart_font_kwargs() == {"font_paths": [str(tmp_path / drawer.CHART_FONT_FILENAMES[0])]}

    request = _chart_request()
    from_json: list[str] = []
    opened: list[str] = []

    class FakeScore:
        @staticmethod
        def from_json(value):
            from_json.append(value)
            return "json-score"

        @staticmethod
        def open(value):
            opened.append(value)
            return "file-score"

    monkeypatch.setattr(drawer, "Score", FakeScore)
    assert drawer.load_score(request.model_copy(update={"chart_json": "{}"})) == "json-score"
    assert from_json == ["{}"]
    assert drawer.load_score(request.model_copy(update={"chart_json": None, "sus_path": "chart.sus"})) == "file-score"
    assert opened[-1].endswith("chart.sus")
    with pytest.raises(ValueError, match="chart_json or sus_path"):
        drawer.load_score(request.model_copy(update={"chart_json": None, "sus_path": None}))


def test_prepare_chart_render_sets_metadata_style_and_constructor_options(tmp_path, monkeypatch):
    style = tmp_path / "style.css"
    style.write_text("note { color: red; }", encoding="utf-8")
    observed = {}

    class FakeScore:
        def set_meta(self, **kwargs):
            observed["meta"] = kwargs

    class FakeDrawing:
        def __init__(self, **kwargs):
            observed["drawing"] = kwargs

    monkeypatch.setattr(drawer, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(drawer, "load_score", lambda _request: FakeScore())
    monkeypatch.setattr(drawer, "Drawing", FakeDrawing)
    monkeypatch.setattr(drawer, "chart_font_kwargs", lambda: {"font_dirs": ["fonts"]})
    request = _chart_request().model_copy(
        update={
            "style_path": "style.css",
            "skill": True,
            "music_meta": False,
            "target_segment_seconds": 12,
        }
    )

    drawing, _score = drawer._prepare_chart_render(request)

    assert isinstance(drawing, FakeDrawing)
    assert observed["drawing"]["style_sheet"] == "note { color: red; }"
    assert observed["drawing"]["font_dirs"] == ["fonts"]
    assert observed["meta"]["title"] == "Custom"
    assert observed["meta"]["jacket"].endswith("jacket.png")


def test_chart_png_size_and_encoded_render_cover_valid_and_invalid_payloads(monkeypatch):
    png = b"\x89PNG\r\n\x1a\n" + (b"\0" * 8) + struct.pack(">II", 9, 6)

    class Drawing:
        def png(self, score):
            assert score == "score"
            return png

    monkeypatch.setattr(drawer, "_prepare_chart_render", lambda _request: (Drawing(), "score"))
    assert drawer.render_chart_png_bytes(object()) is png
    assert drawer._png_size(png) == (9, 6)
    with pytest.raises(ValueError, match="did not return a PNG"):
        drawer._png_size(b"not-png")


@pytest.mark.anyio
async def test_chart_pillow_generation_and_composition_use_loaded_pixels(monkeypatch):
    image = Image.new("RGB", (3, 2), (1, 2, 3))
    encoded = BytesIO()
    image.save(encoded, "PNG")

    async def immediate_pool(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(drawer, "run_in_pool", immediate_pool)
    monkeypatch.setattr(drawer, "render_chart_png_bytes", lambda _request: encoded.getvalue())
    rendered = await drawer.generate_music_chart(_chart_request())
    assert rendered.size == (3, 2)
    assert rendered.getpixel((0, 0)) == (1, 2, 3)

    expected = Image.new("RGBA", (4, 4))
    monkeypatch.setattr(drawer, "generate_music_chart", lambda _request: _async_value(rendered))
    monkeypatch.setattr("src.sekai.base.draw.add_request_watermark_to_image", lambda *_args: _async_value(expected))
    assert await drawer.compose_music_chart_image(_chart_request()) is expected


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
