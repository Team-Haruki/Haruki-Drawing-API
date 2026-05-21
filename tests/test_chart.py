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
