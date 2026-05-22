import pytest

from src.sekai.music.drawer import compose_music_detail_image
from src.sekai.music.model import (
    CustomChartInfo,
    DifficultyInfo,
    MusicDetailRequest,
    MusicMD,
    MusicVocalInfo,
)


@pytest.mark.anyio
async def test_compose_music_detail_image_accepts_custom_chart_info():
    request = MusicDetailRequest(
        region="JP",
        music_info=MusicMD(
            id=582,
            title="Original Song",
            composer="Composer",
            lyricist="Lyricist",
            arranger="Arranger",
            mv_info=[],
            categories=[],
            release_at=1710000000000,
            is_full_length=False,
        ),
        vocal=MusicVocalInfo(vocal_info={}, vocal_assets={}),
        alias=[],
        length="120.0秒",
        bpm=180,
        difficulty=DifficultyInfo(level=[32], note_count=[1234], has_append=False, order=["master"]),
        music_jacket_path="jacket_s_001.png",
        custom_chart_info=CustomChartInfo(
            score_id="poq_4f4bap-_apb5jqtdf4kqkj9o",
            title="Custom Title",
            author="Maker",
            description="A custom chart",
            difficulty="master",
            play_level=32,
            note_count=1234,
            bpm="180 / 210",
            published_at=1710000000000,
            preview_start_time_sec=12.5,
            review_count=23,
            play_count=456,
            full_combo_rate=0.125,
            tags=["mv_2d", "vocaloid"],
        ),
    )

    image = await compose_music_detail_image(request)

    assert image.width > 0
    assert image.height > 0
