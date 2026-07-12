"""Real-payload generator for the music + score domains (10 endpoints).

Replicates Haruki-Cloud's request-body construction offline per
``out/payload-specs/music-score.md``: masterdata + the real suite snapshot
(695 userMusics / 5047 userMusicResults / 17122 userMusicAchievements) +
the production music_metas.json copy (``out/haruki-sekai-master/music_metas.json``,
omakase already injected) + the embedded ``data/custom_room_pt.csv``.

Endpoints: music_detail, music_brief_list, music_list, music_progress,
music_rewards_detail, music_rewards_basic, score_control, score_custom_room,
score_music_meta (JSON array body), score_music_board.
"""

from __future__ import annotations

import csv
from functools import cache
import io
import json
import math
from pathlib import Path
import sys

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[1]))

import common

from src.sekai.music.model import (
    BasicMusicRewardsRequest,
    DetailMusicRewardsRequest,
    MusicBriefListRequest,
    MusicDetailRequest,
    MusicListRequest,
    PlayProgressRequest,
)
from src.sekai.score.model import (
    CustomRoomScoreRequest,
    MusicBoardRequest,
    MusicMetaRequest,
    ScoreControlRequest,
)

MD = common.MD
ASSETS = common.ASSETS

MUSIC_METAS_PATH = common.REPO_ROOT / "out" / "haruki-sekai-master" / "music_metas.json"
CUSTOM_ROOM_CSV_PATH = _HERE / "data" / "custom_room_pt.csv"

# controller.go:17-20
HIDDEN_MUSIC_IDS = {241, 290}
# lookup_cover_bpm.go:408-425
DIFFICULTY_ORDER = {"easy": 1, "normal": 2, "hard": 3, "expert": 4, "master": 5, "append": 6}
# board_helpers.go:62-79
BOARD_PRIORITY = {"master": 6, "append": 5, "expert": 4, "hard": 3, "normal": 2, "easy": 1}
BASE_DIFF_ORDER = ["easy", "normal", "hard", "expert", "master"]

# board_request.go:13-21
BOARD_PAGE_SIZE = 50
BOARD_DEFAULT_POWER = 300000
BOARD_DEFAULT_DECK_BONUS = 400.0
BOARD_DEFAULT_SOLO_SKILL = 1.2
BOARD_DEFAULT_MULTI_SKILL = 2.0
BOARD_DEFAULT_SOLO_INTERVAL = 28.0
BOARD_DEFAULT_MULTI_INTERVAL = 45.2

NOW_MS = common.now_ms()

# ---------------------------------------------------------------------------
# Masterdata lookups
# ---------------------------------------------------------------------------


@cache
def _music_by_id() -> dict[int, dict]:
    return {m["id"]: m for m in MD.get("musics")}


@cache
def _difficulty_map() -> dict[int, dict[str, tuple[int, int]]]:
    """musicId -> {difficulty: (playLevel, totalNoteCount)} (builder_metadata.go:14-64)."""
    out: dict[int, dict[str, tuple[int, int]]] = {}
    for d in MD.get("musicDifficulties"):
        key = str(d.get("musicDifficulty", "")).strip().lower()
        out.setdefault(d["musicId"], {})[key] = (d.get("playLevel", 0), d.get("totalNoteCount", 0))
    return out


def _play_level(music_id: int, diff: str) -> int:
    return _difficulty_map().get(music_id, {}).get(diff, (0, 0))[0]


@cache
def _vocals_by_music() -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for v in MD.get("musicVocals"):
        out.setdefault(v["musicId"], []).append(v)
    return out


@cache
def _tags_by_music() -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for t in MD.get("musicTags"):
        out.setdefault(t["musicId"], []).append(t["musicTag"])
    return out


@cache
def _outside_names() -> dict[int, str]:
    return {c["id"]: c.get("name", "") for c in MD.get("outsideCharacters")}


@cache
def _event_ids_by_music() -> dict[int, list[int]]:
    """musicId -> eventIds, seq ascending (local_musics.go:127-148)."""
    out: dict[int, list[int]] = {}
    for em in sorted(MD.get("eventMusics"), key=lambda e: e.get("seq", 0)):
        out.setdefault(em["musicId"], []).append(em["eventId"])
    return out


@cache
def _limited_by_music() -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for lt in MD.get("limitedTimeMusics"):
        out.setdefault(lt["musicId"], []).append(lt)
    return out


def _character_name(character_id: int) -> str:
    ch = MD.character_by_id().get(character_id)
    if not ch:
        return ""
    return f"{ch.get('firstName', '')}{ch.get('givenName', '')}".strip()


def _primary_event(music_id: int) -> dict | None:
    """Earliest-startAt event among the music's events (local_musics.go:303-328)."""
    earliest = None
    for eid in _event_ids_by_music().get(music_id, []):
        ev = MD.event_by_id().get(eid)
        if ev is None:
            continue
        if earliest is None or ev.get("startAt", 0) < earliest.get("startAt", 0):
            earliest = ev
    return earliest


def _display_title(music: dict) -> str:
    """JP region: base title as-is (builder_helpers.go / builder_metadata.go:145-166)."""
    return music.get("title", "").strip() or music.get("title", "")


def _jacket_path(assetbundle_name: str) -> str:
    return ASSETS.region_asset(f"music/jacket/{assetbundle_name}/{assetbundle_name}.png")


def _event_banner_path(assetbundle_name: str) -> str:
    """helper.go:291-303 candidate triple."""
    abn = assetbundle_name.strip()
    return ASSETS.region_asset(
        f"home/banner/{abn}/{abn}.png",
        f"event/{abn}/banner.png",
        f"event_story/{abn}/screen_image/banner_event_story.png",
    )


def _is_visible(music: dict) -> bool:
    """visibility.go:16-28."""
    return music["id"] not in HIDDEN_MUSIC_IDS and music.get("publishedAt", 0) <= NOW_MS


@cache
def _visible_musics() -> list[dict]:
    return [m for m in MD.get("musics") if _is_visible(m)]


def _display_order(music: dict) -> int:
    """visibility.go:83-94."""
    if music.get("seq", 0) > 0:
        return music["seq"]
    if music.get("publishedAt", 0) > 0:
        return music["publishedAt"]
    return music["id"]


# ---------------------------------------------------------------------------
# Difficulty / categories / vocals (builder_metadata.go)
# ---------------------------------------------------------------------------


def _difficulty_info(music_id: int) -> dict:
    diff_map = _difficulty_map().get(music_id, {})
    levels, notes, order = [], [], []
    for diff in BASE_DIFF_ORDER:
        lv, nc = diff_map.get(diff, (0, 0))
        levels.append(lv)
        notes.append(nc)
        order.append(diff)
    has_append = "append" in diff_map
    if has_append:
        lv, nc = diff_map["append"]
        levels.append(lv)
        notes.append(nc)
        order.append("append")
    return {"level": levels, "note_count": notes, "has_append": has_append, "order": order}


def _categories(music: dict) -> list[str]:
    cats = music.get("categories") or []
    if cats:
        return list(cats)
    return list(_tags_by_music().get(music["id"], []))


# builder_helpers.go:89-129
_CAPTION_OVERRIDES = {
    "セカイver.": "Sekai",
    "セカイ ver.": "Sekai",
    "バーチャル・シンガーver.": "Virtual Singer",
    "バーチャルシンガーver.": "Virtual Singer",
    "アナザーボーカルver.": "Another Vocal",
    "原曲ver.": "Original Song",
    "原曲 ver.": "Original Song",
    "ストリーミングライブver.": "Connect Live",
    "ストリーミングライブ ver.": "Connect Live",
    "エイプリルフールver.": "April Fool",
    "あんさんぶるスターズ！！コラボver.": "Ensemble Stars!! Collab",
    "「劇場版プロジェクトセカイ」ver.": "Movie",
    "sekai ver.": "Sekai",
    "sekai": "Sekai",
    "virtual singer ver.": "Virtual Singer",
    "virtual singer": "Virtual Singer",
    "another vocal ver.": "Another Vocal",
    "another vocal": "Another Vocal",
    "original song ver.": "Original Song",
    "original song": "Original Song",
    "streaming live ver.": "Connect Live",
    "streaming live": "Connect Live",
    "instrumental ver.": "Inst.",
    "instrumental": "Inst.",
    "april fool 2022 ver.": "April Fool",
    "april_fool_2022 ver.": "April Fool",
    "april_fool_2022": "April Fool",
    "april fool": "April Fool",
    "sekai version": "Sekai",
    "virtual singer version": "Virtual Singer",
    "another vocal version": "Another Vocal",
    "original song version": "Original Song",
    "streaming live version": "Connect Live",
    "instrumental version": "Inst.",
    "april fool 2022 version": "April Fool",
    "ensemble stars!! collab": "Ensemble Stars!! Collab",
    "ensemble stars!! collab ver.": "Ensemble Stars!! Collab",
    "movie ver.": "Movie",
    "movie": "Movie",
}

# builder_helpers.go:131-139
_TYPE_FALLBACKS = {
    "sekai": "Sekai",
    "virtual_singer": "Virtual Singer",
    "original_song": "Original Song",
    "another_vocal": "Another Vocal",
    "streaming_live": "Connect Live",
    "instrumental": "Inst.",
    "april_fool_2022": "April Fool",
}

# builder_helpers.go:141-162 (JP entries are identity mappings)
_JP_LOCALIZE = {"sekai": "Sekai", "virtual singer": "Virtual Singer"}


def _localize_caption(caption: str) -> str:
    base = caption.strip()
    if not base:
        return caption
    return _JP_LOCALIZE.get(base.lower(), base)


def _normalize_caption(raw: str, vocal_type: str, assetbundle_name: str) -> str:
    """builder_helpers.go:164-198 normalizeVocalCaption (JP)."""
    trimmed = raw.strip() or vocal_type.strip()
    key = trimmed.lower()
    key = key.replace("　", " ").replace("．", ".").replace("version", "ver.").replace("ver..", "ver.")
    while "  " in key:
        key = key.replace("  ", " ")
    key = key.strip()
    if key.endswith("ver"):
        key += "."
    if key in _CAPTION_OVERRIDES:
        return _localize_caption(_CAPTION_OVERRIDES[key])
    if trimmed:
        return trimmed
    name = assetbundle_name.strip().lower()
    if name.startswith("se_"):
        return _localize_caption("Sekai")
    if name.startswith("vs_"):
        return _localize_caption("Virtual Singer")
    if name.startswith("an_"):
        return _localize_caption("Another Vocal")
    fallback = _TYPE_FALLBACKS.get(vocal_type.strip().lower())
    if fallback:
        return _localize_caption(fallback)
    if key == "virtual singer":
        return _localize_caption("Virtual Singer")
    return trimmed


def _jp_vocal_order_key(vocal: dict) -> str:
    """builder_helpers.go:67-87."""
    base = vocal.get("assetbundleName", "").strip() or "vocal"
    name = vocal.get("assetbundleName", "").strip().lower()
    priority = 90
    if name.startswith("vs_"):
        priority = 10
    elif name.startswith("se_"):
        priority = 20
    elif name.startswith("an_"):
        priority = 30
    return f"{priority:02d}_{base}"


def _vocal_info(music_id: int) -> dict:
    """builder_metadata.go:66-111 buildVocalInfo (JP keys)."""
    info: dict[str, dict] = {}
    assets_map: dict[str, str] = {}
    for vocal in _vocals_by_music().get(music_id, []):
        characters = []
        for ch in vocal.get("characters", []):
            ctype = str(ch.get("characterType", "")).strip().lower()
            if ctype == "outside_character":
                name = _outside_names().get(ch.get("characterId", 0), "").strip()
                use_avatar = False
            else:
                name = _character_name(ch.get("characterId", 0))
                use_avatar = True
            if not name:
                name = "VS"
            characters.append({"characterName": name})
            if use_avatar and ch.get("characterId", 0):
                assets_map[name] = ASSETS.chara_icon(ch["characterId"])
        info[_jp_vocal_order_key(vocal)] = {
            "caption": _normalize_caption(
                vocal.get("caption", ""), vocal.get("musicVocalType", ""), vocal.get("assetbundleName", "")
            ),
            "characters": characters,
        }
    return {"vocal_info": dict(sorted(info.items())), "vocal_assets": assets_map}


# ---------------------------------------------------------------------------
# music_metas pool (meta/loader.go + omakase.go)
# ---------------------------------------------------------------------------


def _inject_omakase(metas: list[dict]) -> list[dict]:
    """meta/omakase.go:9-97; no-op when music_id 10000 already exists."""
    if any(int(m.get("music_id", 0)) == 10000 for m in metas):
        return metas
    scalar_keys = [
        "music_time",
        "event_rate",
        "base_score",
        "base_score_auto",
        "fever_score",
        "fever_end_time",
        "tap_count",
    ]
    slice_keys = ["skill_score_solo", "skill_score_auto", "skill_score_multi"]
    agg: dict[str, object] = dict.fromkeys(scalar_keys, 0.0)
    for k in slice_keys:
        agg[k] = [0.0] * 6
    count = 0
    for item in metas:
        if item.get("difficulty") not in ("master", "expert", "hard"):
            continue
        count += 1
        for k in scalar_keys:
            agg[k] += float(item.get(k, 0.0))
        for k in slice_keys:
            base = agg[k]
            for i, v in enumerate(item.get(k, [])[:6]):
                base[i] += float(v)
    if count == 0:
        return metas
    for k in scalar_keys:
        agg[k] /= count
    agg["event_rate"] = float(int(agg["event_rate"]))
    agg["tap_count"] = float(int(agg["tap_count"]))
    for k in slice_keys:
        agg[k] = [v / count for v in agg[k]]
    for difficulty in ("master", "expert", "hard"):
        metas.append({"music_id": 10000, "difficulty": difficulty, **{k: agg[k] for k in scalar_keys + slice_keys}})
    return metas


@cache
def _music_metas() -> list[dict]:
    return _inject_omakase(json.loads(MUSIC_METAS_PATH.read_text()))


def _meta_info(item: dict) -> dict:
    """chart_meta.go:58-80 MusicMetaInfo conversion (snake_case; fever_end_time dropped)."""
    return {
        "difficulty": str(item.get("difficulty", "")).strip().lower() or "master",
        "music_time": float(item.get("music_time", 0.0)),
        "tap_count": int(item.get("tap_count", 0)),
        "event_rate": float(item.get("event_rate", 0.0)),
        "base_score": float(item.get("base_score", 0.0)),
        "base_score_auto": float(item.get("base_score_auto", 0.0)),
        "skill_score_solo": [float(v) for v in item.get("skill_score_solo", [])],
        "skill_score_auto": [float(v) for v in item.get("skill_score_auto", [])],
        "skill_score_multi": [float(v) for v in item.get("skill_score_multi", [])],
        "fever_score": float(item.get("fever_score", 0.0)),
    }


def _metas_for_music(music_id: int) -> list[dict]:
    """findAllMusicMetas: stable sort by easy->append (chart_meta.go:35-56)."""
    items = [_meta_info(m) for m in _music_metas() if int(m.get("music_id", 0)) == music_id]
    items.sort(key=lambda m: DIFFICULTY_ORDER.get(m["difficulty"], 99))
    return items


@cache
def _metas_by_music() -> dict[int, list[dict]]:
    """board_meta.go:10-47: per music, difficulty priority descending (stable)."""
    out: dict[int, list[dict]] = {}
    for item in _music_metas():
        mid = int(item.get("music_id", 0))
        if mid <= 0:
            continue
        out.setdefault(mid, []).append(_meta_info(item))
    for mid in out:
        out[mid].sort(key=lambda m: -BOARD_PRIORITY.get(m["difficulty"], 0))
    return out


# ---------------------------------------------------------------------------
# Suite music results (snapshot/local_helpers_music.go)
# ---------------------------------------------------------------------------

_RESULT_PRIORITY = {"ap": 3, "fc": 2, "clear": 1}


def _normalize_play_result(item: dict) -> str:
    if item.get("fullPerfectFlg"):
        return "ap"
    if item.get("fullComboFlg"):
        return "fc"
    pr = str(item.get("playResult", "") or "")
    if pr.lower() == "not_clear" or pr == "":
        return "not_clear"
    return "clear"


def _merge_result(store: dict[str, dict[int, str]], diff: str, music_id: int, status: str) -> None:
    bucket = store.setdefault(diff, {})
    if _RESULT_PRIORITY.get(status, 0) >= _RESULT_PRIORITY.get(bucket.get(music_id, ""), 0):
        bucket[music_id] = status


def _collect_flat_results(store: dict[str, dict[int, str]], results: list[dict]) -> None:
    for item in results:
        diff = str(item.get("musicDifficultyType", "") or "").strip().lower()
        if not diff:
            diff = str(item.get("musicDifficulty", "") or "").strip().lower()
        if not diff or not item.get("musicId"):
            continue
        _merge_result(store, diff, item["musicId"], _normalize_play_result(item))


@cache
def _suite_music_results() -> dict[str, dict[int, str]]:
    """Flat userMusicResults + nested userMusics merge (local_helpers_music.go:11-153)."""
    suite = common.load_suite()
    store: dict[str, dict[int, str]] = {}
    _collect_flat_results(store, suite.get("userMusicResults") or [])
    for music in suite.get("userMusics") or []:
        for status in music.get("userMusicDifficultyStatuses") or []:
            results = []
            for item in status.get("userMusicResults") or []:
                merged = dict(item)
                merged.setdefault("musicId", music.get("musicId"))
                if not str(merged.get("musicDifficultyType", "") or "").strip():
                    merged["musicDifficultyType"] = status.get("musicDifficultyType") or status.get("musicDifficulty")
                results.append(merged)
            _collect_flat_results(store, results)
    return store


def _music_results(diff: str) -> dict[int, str]:
    return dict(_suite_music_results().get(diff.strip().lower(), {}))


# ---------------------------------------------------------------------------
# Profiles (snapshot/factory.go + local_service.go + controller_helpers.go)
# ---------------------------------------------------------------------------


def _detailed_profile() -> dict:
    return common.build_user_info(is_hide_uid=True)


def _profile_card(*, bg_alpha: int | None = 80, error_message: str | None = None) -> dict:
    """local_service.go:113-131 + normalizeMusicProfileCard (controller_helpers.go:121-127)."""
    detail = _detailed_profile()
    card: dict = {
        "profile": {
            "id": detail["id"],
            "region": detail["region"],
            "nickname": detail["nickname"],
            "is_hide_uid": detail["is_hide_uid"],
            "leader_image_path": detail["leader_image_path"],
            "has_frame": detail["has_frame"],
        },
        "data_sources": [
            {
                "name": "Suite数据",
                "source": detail["source"],
                "update_time": detail["update_time"],
                "mode": detail.get("mode"),
            }
        ],
    }
    if bg_alpha is not None:
        card["bg_alpha"] = bg_alpha
    if error_message is not None:
        card["error_message"] = error_message
    return card


# ---------------------------------------------------------------------------
# Music board rows (board_request_rows.go / board_metrics.go)
# ---------------------------------------------------------------------------


def _weighted_skill(skill_scores: list[float], sorted_skills: list[float], leader_skill: float) -> float:
    if not skill_scores:
        return 0.0
    core = list(skill_scores)
    extra = 0.0
    if len(core) > 5:
        extra = core[5]
        core = core[:5]
    core.sort(reverse=True)
    total = sum(core[i] * sorted_skills[i] for i in range(min(len(core), len(sorted_skills))))
    if len(skill_scores) > 5:
        total += extra * leader_skill
    return total


def _populate_live_metrics(
    row: dict, live_type: str, score: float, skill_account: float, power: int, deck_bonus: float, play_interval: float
) -> None:
    """board_metrics.go:44-96."""
    active_bonus = 5 * 0.015 * power if live_type == "multi" else 0.0
    real_score = math.floor(score * power * 4 + active_bonus)
    event_rate = row["event_rate"] / 100.0
    deck_rate = deck_bonus / 100.0 + 1
    if live_type in ("solo", "auto"):
        base = 100 + int(real_score / 20000)
        pt = math.floor(base * event_rate * deck_rate)
    else:
        other_score = real_score * 4
        base = 110 + int(real_score / 17000) + min(13, int(other_score / 340000))
        pt = math.floor(base * event_rate * deck_rate)
    total_time = row["music_time"] + play_interval
    play_count_per_hour = 3600 / total_time if total_time > 0 else 0.0
    pt_per_hour = pt * play_count_per_hour
    row[f"{live_type}_score"] = score
    row[f"{live_type}_real_score"] = real_score
    row[f"{live_type}_pt"] = float(pt)
    row[f"{live_type}_skill_account"] = skill_account
    row[f"{live_type}_pt_per_hour"] = pt_per_hour
    if live_type == "solo":
        row["play_count_per_hour"] = play_count_per_hour


def _build_board_rows(
    skills: list[float], strategy: str, power: int, deck_bonus: float, play_interval: float
) -> list[dict]:
    """board_request_rows.go:11-95 (unsorted; caller sorts + ranks)."""
    sorted_skills = list(skills)
    if strategy == "max":
        sorted_skills.sort(reverse=True)
    elif strategy == "min":
        sorted_skills.sort()
    elif strategy == "avg":
        avg = sum(sorted_skills) / len(sorted_skills)
        sorted_skills = [avg] * len(sorted_skills)
    rows: list[dict] = []
    for music_id in sorted(_metas_by_music()):
        music = _music_by_id().get(music_id)
        if music is None:
            continue
        title = _display_title(music)
        cover = _jacket_path(music["assetbundleName"])
        for meta in _metas_by_music()[music_id]:
            level = _play_level(music_id, meta["difficulty"])
            if level <= 0:
                continue
            tps = meta["tap_count"] / meta["music_time"] if meta["music_time"] > 0 else 0.0
            solo_skill = _weighted_skill(meta["skill_score_solo"], sorted_skills, skills[0])
            auto_skill = _weighted_skill(meta["skill_score_auto"], sorted_skills, skills[0])
            multi_skill = _weighted_skill(meta["skill_score_multi"], sorted_skills, skills[0])
            solo_score = meta["base_score"] + solo_skill
            auto_score = meta["base_score_auto"] + auto_skill
            multi_score = meta["base_score"] + multi_skill + meta["fever_score"] * 0.5 + 0.01875
            row = {
                "rank": 0,
                "music_id": music_id,
                "difficulty": meta["difficulty"],
                "level": level,
                "music_title": title,
                "music_cover_path": cover,
                "event_rate": meta["event_rate"],
                "music_time": meta["music_time"],
                "tps": tps,
            }
            _populate_live_metrics(
                row,
                "solo",
                solo_score,
                solo_skill / solo_score if solo_score > 0 else 0.0,
                power,
                deck_bonus,
                play_interval,
            )
            _populate_live_metrics(
                row,
                "auto",
                auto_score,
                auto_skill / auto_score if auto_score > 0 else 0.0,
                power,
                deck_bonus,
                play_interval,
            )
            _populate_live_metrics(
                row,
                "multi",
                multi_score,
                multi_skill / multi_score if multi_score > 0 else 0.0,
                power,
                deck_bonus,
                play_interval,
            )
            rows.append(row)
    return rows


def _board_metric(row: dict, target: str, live_type: str) -> float:
    if target in ("score", "pt"):
        return row[f"{live_type}_{target}"]
    if target == "pt/time":
        return row[f"{live_type}_pt_per_hour"]
    if target == "tps":
        return row["tps"]
    if target == "time":
        return row["music_time"]
    return 0.0


def _sort_board_rows(
    rows: list[dict], target: str, live_type: str, ascend: bool, keep_one_diff_per_music: bool
) -> None:
    """board_metrics.go:98-129 (in-place sort + rank assignment)."""

    def key(row: dict) -> tuple:
        metric = _board_metric(row, target, live_type)
        priority = BOARD_PRIORITY.get(row["difficulty"], 0)
        return (metric, -priority) if ascend else (-metric, -priority)

    rows.sort(key=key)
    if keep_one_diff_per_music:
        seen: set[int] = set()
        rank = 1
        for row in rows:
            if row["music_id"] in seen:
                row["rank"] = 0
                continue
            seen.add(row["music_id"])
            row["rank"] = rank
            rank += 1
        return
    for idx, row in enumerate(rows):
        row["rank"] = idx + 1


# ---------------------------------------------------------------------------
# 1. music/detail (builder_requests.go:16-62 + detail_meta.go)
# ---------------------------------------------------------------------------

DETAIL_MUSIC_ID = 187  # ロウワー: event + append + outside-character vocals + 6 vocal versions

_LEADERBOARD_LIVE_ORDER = ["solo", "multi", "auto"]
_LEADERBOARD_TARGET_ORDER = ["score", "pt", "pt/time"]


def _leaderboard_value(row: dict, live_type: str, target: str) -> str:
    """detail_meta.go:178-192."""
    if target == "score":
        return f"{row[f'{live_type}_score'] * 100:.1f}%"
    if target == "pt":
        return str(round(row[f"{live_type}_pt"]))
    if target == "pt/time":
        return f"{row[f'{live_type}_pt_per_hour'] / 10000.0:.2f}w/h"
    return "-"


def _detail_leaderboard(music_id: int) -> tuple[list[list[dict | None]], int]:
    """detail_meta.go:117-209 (fixed params, avg strategy, keep-one-diff ranking)."""
    matrix: list[list[dict | None]] = []
    total_songs = 0
    skills_by_live = {
        "solo": [BOARD_DEFAULT_SOLO_SKILL] * 5,
        "auto": [BOARD_DEFAULT_SOLO_SKILL] * 5,
        "multi": [BOARD_DEFAULT_MULTI_SKILL] * 5,
    }
    intervals = {
        "solo": BOARD_DEFAULT_SOLO_INTERVAL,
        "auto": BOARD_DEFAULT_SOLO_INTERVAL,
        "multi": BOARD_DEFAULT_MULTI_INTERVAL,
    }
    for live_type in _LEADERBOARD_LIVE_ORDER:
        rows = _build_board_rows(
            skills_by_live[live_type], "avg", BOARD_DEFAULT_POWER, BOARD_DEFAULT_DECK_BONUS, intervals[live_type]
        )
        if not rows:
            return [], 0
        row_matrix: list[dict | None] = []
        for target in _LEADERBOARD_TARGET_ORDER:
            sorted_rows = [dict(r) for r in rows]
            _sort_board_rows(sorted_rows, target, live_type, ascend=False, keep_one_diff_per_music=True)
            ranked = sum(1 for r in sorted_rows if r["rank"] > 0)
            total_songs = max(total_songs, ranked)
            info = next(
                (
                    {"rank": r["rank"], "diff": r["difficulty"], "value": _leaderboard_value(r, live_type, target)}
                    for r in sorted_rows
                    if r["music_id"] == music_id and r["rank"] > 0
                ),
                None,
            )
            row_matrix.append(info)
        matrix.append(row_matrix)
    return matrix, total_songs


def gen_music_detail() -> str:
    music = _music_by_id()[DETAIL_MUSIC_ID]
    categories = _categories(music)
    body: dict = {
        "region": common.REGION.upper(),
        "music_info": {
            "id": music["id"],
            "title": _display_title(music),
            "composer": music.get("composer", ""),
            "lyricist": music.get("lyricist", ""),
            "arranger": music.get("arranger", ""),
            "mv_info": list(categories),
            "categories": categories,
            "release_at": music.get("publishedAt", 0),
            "is_full_length": music.get("isFullLength", False),
        },
        "difficulty": _difficulty_info(music["id"]),
        "vocal": _vocal_info(music["id"]),
        "music_jacket_path": _jacket_path(music["assetbundleName"]),
        "alias": [],
    }
    event = _primary_event(music["id"])
    if event is not None:
        body["event_id"] = event["id"]
        banner = _event_banner_path(event.get("assetbundleName", ""))
        if banner:
            body["event_banner_path"] = banner
    limited = _limited_by_music().get(music["id"], [])
    if limited:
        body["limited_times"] = [[lt["startAt"], lt["endAt"]] for lt in limited]
    metas = _metas_for_music(music["id"])
    if metas:
        max_seconds = max(m["music_time"] for m in metas)
        if max_seconds > 0:
            minutes = int(max_seconds) // 60
            remain = max_seconds - minutes * 60
            body["length"] = f"{max_seconds:.1f}秒（{minutes}分{remain:.1f}秒）"
    # BPM requires local sus charts (music/music_score/0187_01/*.txt) which are unavailable
    # offline; Cloud omits the field in that case (detail_meta.go:63-84).
    matrix, total = _detail_leaderboard(music["id"])
    if matrix and total > 0:
        body["leaderboard_matrix"] = matrix
        body["leaderboard_music_num"] = total
        body["leaderboard_live_types"] = {"solo": "单人", "multi": "多人", "auto": "AUTO"}
        body["leaderboard_targets"] = {"score": "分数", "pt": "PT", "pt/time": "时速"}
    MusicDetailRequest.model_validate(body)
    common.write_payload("music_detail", body)
    return "music_detail"


# ---------------------------------------------------------------------------
# 2. music/brief-list (builder_requests.go:116-194, ambiguous /查歌 list flow)
# ---------------------------------------------------------------------------


def gen_music_brief_list() -> str:
    visible = _visible_musics()
    step = max(1, len(visible) // 24)
    picked = visible[::step][:24]
    music_list = []
    for music in picked:
        diff_info = _difficulty_info(music["id"])
        music_list.append(
            {
                "id": music["id"],
                "level": max(diff_info["level"]),
                "music_jacket_path": _jacket_path(music["assetbundleName"]),
                "music_info": {
                    "id": music["id"],
                    "title": _display_title(music),
                    "composer": music.get("composer", ""),
                    "lyricist": music.get("lyricist", ""),
                    "arranger": music.get("arranger", ""),
                    "categories": _categories(music),
                    "release_at": music.get("publishedAt", 0),
                    "is_full_length": music.get("isFullLength", False),
                },
                "difficulty": diff_info,
            }
        )
    body = {
        "music_list": music_list,
        "region": common.REGION,  # lowercase for brief-list (builder_requests.go:108-113)
        # items carry no per-item difficulty -> required_difficulty is not set by Cloud
        "title": "匹配到多个歌曲，请使用 /查歌 <id> 查询：",
        "title_shadow": True,
    }
    MusicBriefListRequest.model_validate(body)
    common.write_payload("music_brief_list", body)
    return "music_brief_list"


# ---------------------------------------------------------------------------
# 3. music/list (controller_detail_list.go:138-276, suite mode, diff=master)
# ---------------------------------------------------------------------------


def gen_music_list() -> str:
    diff = "master"
    user_results = _music_results(diff)
    music_list = []
    jackets: dict[int, str] = {}
    for music in MD.get("musics"):
        if not _is_visible(music):
            continue
        level = _play_level(music["id"], diff)
        if level == 0:
            continue
        music_list.append(
            {"id": music["id"], "difficulty": level, "difficulty_type": diff, "release_at": _display_order(music)}
        )
        jackets[music["id"]] = _jacket_path(music["assetbundleName"])
    music_list.sort(
        key=lambda e: (DIFFICULTY_ORDER.get(e["difficulty_type"], 99), e["difficulty"], e["release_at"], e["id"])
    )
    body = {
        "user_results": user_results,
        "music_list": music_list,
        "jackets_path_list": jackets,
        "required_difficulties": diff,
        "profile": _detailed_profile(),
        "play_result_icon_path_map": {
            "not_clear": ASSETS.static("icon_not_clear.png"),
            "clear": ASSETS.static("icon_clear.png"),
            "fc": ASSETS.static("icon_fc.png"),
            "ap": ASSETS.static("icon_ap.png"),
        },
    }
    MusicListRequest.model_validate(body)
    common.write_payload("music_list", body)
    return "music_list"


# ---------------------------------------------------------------------------
# 4. music/progress (controller_helpers.go:158-211, cascading counts)
# ---------------------------------------------------------------------------


def gen_music_progress() -> str:
    diff = "master"
    results = _music_results(diff)
    count_map: dict[int, dict] = {}
    for music in MD.get("musics"):
        if not _is_visible(music):
            continue
        level = _play_level(music["id"], diff)
        if level == 0:
            continue
        entry = count_map.setdefault(level, {"level": level, "total": 0, "not_clear": 0, "clear": 0, "fc": 0, "ap": 0})
        entry["total"] += 1
        result = results.get(music["id"], "")
        if result == "ap":
            entry["ap"] += 1
            entry["fc"] += 1
            entry["clear"] += 1
        elif result == "fc":
            entry["fc"] += 1
            entry["clear"] += 1
        elif result == "clear":
            entry["clear"] += 1
        else:
            entry["not_clear"] += 1
    body = {
        "counts": [count_map[level] for level in sorted(count_map)],
        "difficulty": diff,
        "profile": _profile_card(),
    }
    PlayProgressRequest.model_validate(body)
    common.write_payload("music_progress", body)
    return "music_progress"


# ---------------------------------------------------------------------------
# 5/6. music/rewards (rewards.go)
# ---------------------------------------------------------------------------

_RANK_REWARDS = {1: 10, 2: 20, 3: 30, 4: 50}
# jewel (shard for append) portion of the combo achievement tables (rewards.go:33-70)
_COMBO_REWARDS = {
    "hard": {13: 0, 14: 0, 15: 0, 16: 50},
    "expert": {17: 0, 18: 0, 19: 20, 20: 50},
    "master": {21: 0, 22: 0, 23: 20, 24: 50},
    "append": {25: 0, 26: 0, 27: 5, 28: 10},
}
_REWARD_DIFFS = ["hard", "expert", "master", "append"]


def _valid_reward_music_ids() -> set[int]:
    """rewards.go:249-296."""
    result = set()
    for music in MD.get("musics"):
        if not _is_visible(music):
            continue
        limited = _limited_by_music().get(music["id"], [])
        if limited and not any(lt["startAt"] <= NOW_MS < lt["endAt"] for lt in limited):
            continue
        diff_map = _difficulty_map().get(music["id"], {})
        if not any(diff_map.get(d, (0, 0))[0] > 0 for d in [*BASE_DIFF_ORDER, "append"]):
            continue
        result.add(music["id"])
    return result


def gen_music_rewards_detail() -> str:
    valid = _valid_reward_music_ids()
    ach_by_music: dict[int, set[int]] = {}
    for item in common.load_suite().get("userMusicAchievements") or []:
        mid = item.get("musicId", 0)
        if mid in valid:
            ach_by_music.setdefault(mid, set()).add(item.get("musicAchievementId", 0))
    rank_rewards = 0
    combo: dict[str, dict[int, int]] = {d: {} for d in _REWARD_DIFFS}
    for mid in valid:
        achieved = ach_by_music.get(mid, set())
        rank_rewards += sum(jewel for aid, jewel in _RANK_REWARDS.items() if aid not in achieved)
        for diff in _REWARD_DIFFS:
            level = _play_level(mid, diff)
            if level == 0:
                continue
            missing = sum(v for aid, v in _COMBO_REWARDS[diff].items() if aid not in achieved)
            combo[diff][level] = combo[diff].get(level, 0) + missing
    body = {
        "rank_rewards": rank_rewards,
        "combo_rewards": {
            diff: [
                {"level": level, "reward": combo[diff][level]}
                for level in sorted(combo[diff])
                if combo[diff][level] > 0
            ]
            for diff in _REWARD_DIFFS
        },
        "profile": _profile_card(),
        "jewel_icon_path": ASSETS.static("jewel.png"),
        "shard_icon_path": ASSETS.static("shard.png"),
    }
    DetailMusicRewardsRequest.model_validate(body)
    common.write_payload("music_rewards_detail", body)
    return "music_rewards_detail"


def gen_music_rewards_basic() -> str:
    valid = _valid_reward_music_ids()
    music_num = len(valid)
    append_music_num = sum(1 for mid in valid if _play_level(mid, "append") > 0)
    # userMusicDifficultyClearCounts normally comes from the public profile API (offline
    # unavailable); synthesize the same shape from the suite's own play results.
    clear_by_diff: dict[str, int] = {}
    fc_by_diff: dict[str, int] = {}
    for diff in [*BASE_DIFF_ORDER, "append"]:
        results = _music_results(diff)
        clear_by_diff[diff] = sum(1 for v in results.values() if v in ("clear", "fc", "ap"))
        fc_by_diff[diff] = sum(1 for v in results.values() if v in ("fc", "ap"))
    rank_s_num = min(max(clear_by_diff.values(), default=0), music_num)

    def fmt(single: int, count: int) -> str:
        count = max(count, 0)
        return f"{single * count} ({single}×{count})"

    combo_rewards = {}
    for diff in _REWARD_DIFFS:
        total_per_music = sum(_COMBO_REWARDS[diff].values())
        target_count = append_music_num if diff == "append" else music_num
        combo_rewards[diff] = fmt(total_per_music, target_count - fc_by_diff.get(diff, 0))
    body = {
        "rank_rewards": fmt(sum(_RANK_REWARDS.values()), music_num - rank_s_num),
        "combo_rewards": combo_rewards,
        "profile": _profile_card(error_message="当前未使用 Suite 抓包数据，以下为基于公开信息的估算结果。"),
        "jewel_icon_path": ASSETS.static("jewel.png"),
        "shard_icon_path": ASSETS.static("shard.png"),
    }
    BasicMusicRewardsRequest.model_validate(body)
    common.write_payload("music_rewards_basic", body)
    return "music_rewards_basic"


# ---------------------------------------------------------------------------
# 7. score/control (requestbuilder/score_control.go)
# ---------------------------------------------------------------------------

SCORE_CONTROL_MUSIC_ID = 74  # Cloud default when the query omits a song (score_control.go:15)
SCORE_CONTROL_MAX_SCORE = 2840000
SCORE_CONTROL_MAX_EVENT_BONUS = 435
SCORE_CONTROL_MAX_SOLUTIONS = 150
_BOOST_BONUS = {0: 1, 1: 5, 2: 10, 3: 15, 4: 20, 5: 25, 6: 27, 7: 29, 8: 31, 9: 33, 10: 35}


def _select_basic_point(metas: list[dict]) -> int:
    """score_control.go:127-143: master event_rate, else max event_rate."""
    if not metas:
        return 0
    best = metas[0]
    for item in metas[1:]:
        if item["difficulty"] == "master" and best["difficulty"] != "master":
            best = item
            continue
        if best["difficulty"] != "master" and item["event_rate"] > best["event_rate"]:
            best = item
    return int(best["event_rate"])


def _calc_control_points(score: int, event_bonus: int, basic_point: int, boost: int) -> int:
    """score_control.go:203-207 (Go integer division throughout)."""
    base = (100 + score // 20000) * (100 + event_bonus) * basic_point // 10000
    return base * _BOOST_BONUS[boost]


def _find_valid_score_ranges(target: int, basic_point: int, max_event_bonus: int, limit: int) -> list[dict]:
    """score_control.go:145-201 (twin binary searches over [0, 2840000])."""
    result = []
    for event_bonus in range(max_event_bonus + 1):
        for boost in range(11):
            boost_bonus = _BOOST_BONUS[boost]
            if target % boost_bonus != 0:
                continue
            left, right = 0, SCORE_CONTROL_MAX_SCORE
            found = False
            while left <= right:
                mid = (left + right) // 2
                points = _calc_control_points(mid, event_bonus, basic_point, boost)
                if points <= target:
                    left = mid + 1
                    if points == target:
                        found = True
                    continue
                right = mid - 1
            if not found:
                continue
            score_max = right
            left, right = 0, SCORE_CONTROL_MAX_SCORE
            while left <= right:
                mid = (left + right) // 2
                if _calc_control_points(mid, event_bonus, basic_point, boost) >= target:
                    right = mid - 1
                    continue
                left = mid + 1
            result.append({"event_bonus": event_bonus, "boost": boost, "score_min": left, "score_max": score_max})
            if limit > 0 and len(result) >= limit:
                return result
    return result


def gen_score_control() -> str:
    music = _music_by_id()[SCORE_CONTROL_MUSIC_ID]
    metas = _metas_for_music(music["id"])
    basic_point = _select_basic_point(metas)
    target_point = 0
    valid_scores: list[dict] = []
    for candidate in (10000, 12500, 7500, 8000, 15000, 5000):
        rows = _find_valid_score_ranges(
            candidate, basic_point, SCORE_CONTROL_MAX_EVENT_BONUS, SCORE_CONTROL_MAX_SOLUTIONS
        )
        if len(rows) >= len(valid_scores):
            target_point, valid_scores = candidate, rows
        if len(rows) >= 20:
            target_point, valid_scores = candidate, rows
            break
    body = {
        "music_cover_path": _jacket_path(music["assetbundleName"]),
        "music_id": music["id"],
        "music_title": _display_title(music),
        "music_basic_point": basic_point,
        "target_point": target_point,
        "valid_scores": valid_scores,
    }
    ScoreControlRequest.model_validate(body)
    common.write_payload("score_control", body)
    return "score_control"


# ---------------------------------------------------------------------------
# 8. score/custom-room (requestbuilder/custom_room_score.go + music/custom_room.go)
# ---------------------------------------------------------------------------

CUSTOM_ROOM_MUSIC_PER_RATE = 3
CUSTOM_ROOM_MAX_PAIRS = 150


@cache
def _custom_room_table() -> tuple[list[int], list[tuple[int, list[int]]]]:
    """(bonuses, [(event_rate, row_values)]) from the embedded CSV."""
    raw = CUSTOM_ROOM_CSV_PATH.read_text(encoding="utf-8-sig")
    records = list(csv.reader(io.StringIO(raw)))
    bonuses = []
    for cell in records[0][1:]:
        cell = cell.strip().removesuffix("%")
        bonuses.append(int(cell) if cell.lstrip("-").isdigit() else 0)
    rows = []
    for record in records[1:]:
        if not record:
            continue
        head = record[0].strip()
        if not head.isdigit() or int(head) <= 0:
            continue
        values = []
        for cell in record[1 : len(bonuses) + 1]:
            cell = cell.strip()
            values.append(int(cell) if cell.lstrip("-").isdigit() else None)
        rows.append((int(head), values))
    return bonuses, rows


@cache
def _custom_room_music_by_rate() -> dict[int, list[dict]]:
    """music/custom_room.go:11-99: master metas, rounded event_rate, <=3 per rate, meta order."""
    music_by_id = {}
    for music in MD.get("musics"):
        if _is_visible(music):
            music_by_id[music["id"]] = music
    result: dict[int, list[dict]] = {}
    seen: dict[int, set[int]] = {}
    for item in _music_metas():
        if str(item.get("difficulty", "")).strip().lower() != "master":
            continue
        rate = round(float(item.get("event_rate", 0.0)))
        if len(result.get(rate, [])) >= CUSTOM_ROOM_MUSIC_PER_RATE:
            continue
        music = music_by_id.get(int(item.get("music_id", 0)))
        if music is None:
            continue
        if music["id"] in seen.setdefault(rate, set()):
            continue
        seen[rate].add(music["id"])
        result.setdefault(rate, []).append(
            {
                "music_id": music["id"],
                "music_title": _display_title(music),
                "music_cover": _jacket_path(music["assetbundleName"]),
            }
        )
    return result


def _custom_room_pairs(target: int) -> list[list[int]]:
    bonuses, rows = _custom_room_table()
    pairs = [[rate, bonuses[idx]] for rate, values in rows for idx, pt in enumerate(values) if pt == target]
    pairs.sort(key=lambda p: (p[1], -p[0]))
    return pairs


def gen_score_custom_room() -> str:
    _bonuses, rows = _custom_room_table()
    music_by_rate = _custom_room_music_by_rate()
    # pick the target with the most usable (rate has songs) pairs; ties -> smaller target
    best_target, best_count = 0, -1
    for target in sorted({pt for _, values in rows for pt in values if pt}):
        count = sum(1 for pair in _custom_room_pairs(target) if music_by_rate.get(pair[0]))
        if count > best_count:
            best_target, best_count = target, count
    target_point = best_target
    filtered_pairs = []
    for pair in _custom_room_pairs(target_point):
        if not music_by_rate.get(pair[0]):
            continue
        filtered_pairs.append(pair)
        if len(filtered_pairs) >= CUSTOM_ROOM_MAX_PAIRS:
            break
    music_list_map = {}
    for rate, _bonus in filtered_pairs:
        music_list_map.setdefault(rate, music_by_rate[rate])
    body = {
        "target_point": target_point,
        "candidate_pairs": filtered_pairs,
        "music_list_map": music_list_map,
    }
    CustomRoomScoreRequest.model_validate(body)
    common.write_payload("score_custom_room", body)
    return "score_custom_room"


# ---------------------------------------------------------------------------
# 9. score/music-meta (music/meta_request.go; body is a JSON array)
# ---------------------------------------------------------------------------


def gen_score_music_meta() -> str:
    newest = max(
        (m for m in _visible_musics() if _metas_for_music(m["id"])),
        key=lambda m: (m.get("publishedAt", 0), m["id"]),
    )
    elements = []
    for mid in (SCORE_CONTROL_MUSIC_ID, DETAIL_MUSIC_ID, newest["id"]):
        music = _music_by_id()[mid]
        element = {
            "music_id": mid,
            "music_title": _display_title(music),
            "music_cover_path": _jacket_path(music["assetbundleName"]),
            "metas": _metas_for_music(mid),
        }
        MusicMetaRequest.model_validate(element)
        elements.append(element)
    # request_dt.go:14-60: array bodies get timezone/dt injected per element;
    # common.write_payload only handles dicts, so replicate its formatting here.
    common.OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = common.OUT_DIR / "score_music_meta.json"
    path.write_text(json.dumps([common.finalize(e) for e in elements], ensure_ascii=False, indent=1))
    return "score_music_meta"


# ---------------------------------------------------------------------------
# 10. score/music-board (board_request.go, multi + pt/time defaults, spec music)
# ---------------------------------------------------------------------------


def gen_score_music_board() -> str:
    live_type, target, ascend, page = "multi", "pt/time", False, 1
    strategy = "avg"  # board_request_query.go: default for non-solo
    skills = [BOARD_DEFAULT_MULTI_SKILL] * 5
    power, deck_bonus, interval = BOARD_DEFAULT_POWER, BOARD_DEFAULT_DECK_BONUS, BOARD_DEFAULT_MULTI_INTERVAL
    rows = _build_board_rows(skills, strategy, power, deck_bonus, interval)
    _sort_board_rows(rows, target, live_type, ascend, keep_one_diff_per_music=False)

    specs = [(SCORE_CONTROL_MUSIC_ID, "master")]
    spec_rows = []
    spec_ranks = set()
    for spec_mid, spec_diff in specs:
        row = next((r for r in rows if r["music_id"] == spec_mid and r["difficulty"] == spec_diff), None)
        if row is not None:
            spec_rows.append(row)
            spec_ranks.add(row["rank"])
    filtered = [r for r in rows if r["rank"] not in spec_ranks]

    show_rows = list(spec_rows)
    remaining = BOARD_PAGE_SIZE - len(show_rows)
    total_page = 1
    if filtered and remaining > 0:
        total_page = math.ceil(len(filtered) / remaining)
        start = (page - 1) * remaining
        show_rows.extend(filtered[start : start + remaining])
    show_rows.sort(key=lambda r: r["rank"])

    items = [
        {
            "rank": r["rank"],
            "music_id": r["music_id"],
            "difficulty": r["difficulty"],
            "level": r["level"],
            "music_title": r["music_title"],
            "music_cover_path": r["music_cover_path"],
            "live_type_pt": r[f"{live_type}_pt"],
            "live_type_real_score": r[f"{live_type}_real_score"],
            "live_type_score": r[f"{live_type}_score"],
            "live_type_skill_account": r[f"{live_type}_skill_account"],
            "live_type_pt_per_hour": r[f"{live_type}_pt_per_hour"],
            "play_count_per_hour": r["play_count_per_hour"],
            "event_rate": r["event_rate"],
            "music_time": r["music_time"],
            "tps": r["tps"],
        }
        for r in show_rows
    ]

    # board_helpers.go:10-56
    title_text = f"多人LIVE歌曲排行 - 活动PT/时间 {'升序' if ascend else '降序'} - 第{page}页/共{total_page}页"
    description = "  |  ".join(
        [f"实效 {skills[0] * 100:.0f}%", f"综合 {power}", f"加成 {deck_bonus:.0f}%", f"间隔 {interval:.1f}s"]
    )
    body = {
        "live_type": live_type,
        "target": target,
        "ascend": ascend,
        "page": page,
        "total_page": total_page,
        "title_text": title_text,
        "items": items,
        "spec_mid_diffs": [[mid, diff] for mid, diff in specs],
        "description": description,
    }
    MusicBoardRequest.model_validate(body)
    common.write_payload("score_music_board", body)
    return "score_music_board"


# ---------------------------------------------------------------------------


def generate() -> list[str]:
    return [
        gen_music_detail(),
        gen_music_brief_list(),
        gen_music_list(),
        gen_music_progress(),
        gen_music_rewards_detail(),
        gen_music_rewards_basic(),
        gen_score_control(),
        gen_score_custom_room(),
        gen_score_music_meta(),
        gen_score_music_board(),
    ]


if __name__ == "__main__":
    names = generate()
    print("written:", names)  # noqa: T201
    common.ASSETS.save_manifest()
    print("assets used:", len(common.ASSETS.used), "missing:", len(common.ASSETS.missing))  # noqa: T201
