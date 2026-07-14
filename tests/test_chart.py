import struct

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
