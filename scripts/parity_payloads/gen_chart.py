"""Chart endpoint payload: mirrors Cloud's BuildMusicChartRequest
(render/music/builder_requests.go:206-238) for music 1 / expert, the only chart whose
sus + jacket + chart_asset statics are synced locally."""

from __future__ import annotations

from pathlib import Path
import sys

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[1]))

import common

MUSIC_ID = 1
DIFFICULTY = "expert"


def generate() -> list[str]:
    music = next(m for m in common.MD.get("musics") if m["id"] == MUSIC_ID)
    play_level = next(
        d["playLevel"]
        for d in common.MD.get("musicDifficulties")
        if d["musicId"] == MUSIC_ID and d["musicDifficulty"] == DIFFICULTY
    )
    abn = music["assetbundleName"]
    composer = (music.get("composer") or "").strip()
    arranger = (music.get("arranger") or "").strip()
    artist = composer if composer == arranger else " / ".join(x for x in (composer, arranger) if x)
    body = {
        "music_id": MUSIC_ID,
        "title": music["title"],
        "artist": artist,
        "difficulty": DIFFICULTY,
        "play_level": play_level,
        "skill": False,
        "jacket_path": common.ASSETS.region_asset(f"music/jacket/{abn}/{abn}.png"),
        "sus_path": common.ASSETS.region_asset(f"music/music_score/{MUSIC_ID:04d}_01/{DIFFICULTY}.txt"),
        "style_path": common.ASSETS.static("chart_asset/css/black.css"),  # chartstyle.Default
        "note_host": "static_images/chart_asset/notes",
        "target_segment_seconds": 6.0,
    }
    from src.sekai.chart.model import GenerateMusicChartRequest

    GenerateMusicChartRequest.model_validate(body)
    common.write_payload("chart", body)
    return ["chart"]


if __name__ == "__main__":
    print("written:", generate())  # noqa: T201
    common.ASSETS.save_manifest()
