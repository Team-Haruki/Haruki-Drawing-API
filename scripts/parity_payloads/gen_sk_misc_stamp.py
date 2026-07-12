"""Real-payload generator for the sk / misc / stamp / help domains.

Replicates Haruki-Cloud's request construction per out/payload-specs/sk-misc-stamp.md.
The sk domain is driven by Cloud's internal tracker (external API), so the rank/trace
data points are synthesized here with realistic shapes (hundreds of points spanning
multiple days, minute-level trace granularity, diurnal activity rhythm); masterdata
only contributes event metadata, world-bloom chapter windows and asset paths.
"""

from datetime import datetime, timedelta, timezone
from itertools import pairwise
import math
from pathlib import Path
import random
import sys

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[1]))
import common

from src.sekai.misc.model import (
    AliasListRequest,
    CharaBirthdayRequest,
    CommandHelpRenderRequest,
)
from src.sekai.sk.model import (
    CFRequest,
    CSBRequest,
    PlayerTraceRequest,
    RankTraceRequest,
    SklRequest,
    SKRequest,
    SpeedRequest,
    WinRateRequest,
)
from src.sekai.stamp.model import StampListRequest

# Fixed "now" for determinism (2026-07-12 12:00:00 Asia/Shanghai), inside event 210's window.
NOW_MS = 1_783_828_800_000
EVENT_ID = 210  # marathon 褪せない今を、彩って (2026-07-09 ~ 2026-07-17)
WL_EVENT_ID = 207  # world_bloom Into the New Light
WL_CHAPTER_CID = 24  # chapter 1 gameCharacterId (Luka)
CC_EVENT_ID = 132  # cheerful_carnival みんなで配信♡WEDDING LIVE！

# handler/sk_parse.go:212-230
DEFAULT_NORMAL_RANKS = [
    *range(1, 11), 20, 30, 40, 50, 100, 200, 300, 400, 500,
    1000, 1500, 2000, 2500, 3000, 4000, 5000,
    10000, 20000, 30000, 40000, 50000, 100000, 200000, 300000,
]
DEFAULT_WL_RANKS = [
    *range(1, 11), 20, 30, 40, 50, 100, 200, 300, 400, 500,
    1000, 2000, 3000, 4000, 5000, 7000,
    10000, 20000, 30000, 40000, 50000, 70000, 100000,
]
CFL_RANKS = [*range(1, 11), 20, 30, 40, 50, 100]

PLAYER_NAMES = [
    "ろん@イベラン中", "セカイに一番近い場所", "全人類ネネロボ化計画", "ミズキと添い遂げる会",
    "夜更かしカナデ", "こはねぇ〜〜!", "P茄子の逆襲", "翠のマグロ", "ツカサ様の下僕No.1",
    "えむ〜ワンダショ!!", "Karin_pjsk", "sleepy_mfy", "初音ミクの消失", "白石杏推し二号",
    "アイル/イベント休み", "月夜のセレナーデ", "みのり号泣した", "レンきゅんprpr", "ネネカスと呼ばないで",
    "咲希ちゃんの笑顔守り隊", "ルカ様の靴舐め係", "シホの弦", "奏でる世界", "エナドリ絵名",
    "瑞希の秘密", "無敵のえむ", "天馬家の食卓", "MEIKOお姉さん", "冬弥の直球", "宵崎奏Fansclub",
    "彰人のカレーうどん", "青柳recipe", "24時間ワンダショ営業中", "風鈴と司", "遥かなるアイドル道",
]

# Current (70h in) border anchors for event 210; log-log interpolated in between.
_BORDER_ANCHORS = [
    (1, 24_500_000), (2, 21_800_000), (3, 19_600_000), (5, 16_800_000), (10, 13_200_000),
    (20, 10_400_000), (50, 7_800_000), (100, 6_300_000), (200, 4_900_000), (500, 3_400_000),
    (1000, 2_500_000), (2000, 1_780_000), (5000, 1_050_000), (10000, 660_000), (30000, 285_000),
    (50000, 178_000), (100000, 84_000), (200000, 27_500), (300000, 9_800),
]

JST = timezone(timedelta(hours=9))
CST = timezone(timedelta(hours=8))


# ---------------------------------------------------------------------------
# Synthetic tracker data (CloudRankInfo shapes, sk-misc-stamp.md §1.0/§4)
# ---------------------------------------------------------------------------


def border_now(rank: int, scale: float = 1.0) -> int:
    if rank <= _BORDER_ANCHORS[0][0]:
        return int(_BORDER_ANCHORS[0][1] * scale)
    for (r0, s0), (r1, s1) in pairwise(_BORDER_ANCHORS):
        if r0 <= rank <= r1:
            t = (math.log(rank) - math.log(r0)) / (math.log(r1) - math.log(r0))
            return int(math.exp(math.log(s0) + t * (math.log(s1) - math.log(s0))) * scale)
    return int(_BORDER_ANCHORS[-1][1] * scale)


def _diurnal(ts_ms: int) -> float:
    """Grinding intensity by JST hour (nights are quiet)."""
    hour = datetime.fromtimestamp(ts_ms / 1000, JST).hour
    if 2 <= hour < 7:
        return 0.12
    if 7 <= hour < 9 or hour < 2:
        return 0.5
    return 1.0


def border_series(rank: int, start_ms: int, end_ms: int, step_ms: int, rng: random.Random,
                  scale: float = 1.0) -> list[tuple[int, int]]:
    """Monotonic (time, score) border walk from 0 at event start to border_now at end."""
    times = list(range(start_ms, end_ms + 1, step_ms))
    weights = [_diurnal(t) * rng.uniform(0.85, 1.15) for t in times[1:]]
    total = sum(weights)
    factor = border_now(rank, scale) / total
    series, cum = [(times[0], 0)], 0.0
    for t, w in zip(times[1:], weights, strict=True):
        cum += w
        series.append((t, int(cum * factor)))
    return series


def synth_minute_trace(name: str, start_ms: int, end_ms: int, breaks: list[tuple[int, int]],
                       rng: random.Random, *, step_ms: int = 60_000, start_score: int = 0,
                       round_pt: tuple[int, int] = (28_000, 42_000),
                       round_gap: tuple[int, int] = (150, 280),
                       rank_start: int = 88, rank_end: int = 24) -> list[dict]:
    """A player's tracker trace: score jumps once per game round, flat while parked."""
    points: list[dict] = []
    score = start_score
    next_round_at = start_ms + rng.randint(*round_gap) * 1000
    total = max(end_ms - start_ms, 1)
    for t in range(start_ms, end_ms + 1, step_ms):
        in_break = any(bs <= t < be for bs, be in breaks)
        while not in_break and next_round_at <= t:
            score += rng.randint(*round_pt)
            next_round_at += rng.randint(*round_gap) * 1000
        if in_break:
            for bs, be in breaks:
                if bs <= t < be:
                    next_round_at = max(next_round_at, be + rng.randint(60, 200) * 1000)
        frac = (t - start_ms) / total
        rank = round(rank_start + (rank_end - rank_start) * frac + rng.uniform(-1.5, 1.5))
        points.append({"rank": max(1, min(100, rank)), "name": name, "score": score, "time": t})
    return points


def derive_metrics(points: list[dict], now_ms: int) -> dict:
    """Local metrics derivation (controller_tracker_metrics.go:19-181)."""
    deltas = [(b["time"], b["score"] - a["score"]) for a, b in pairwise(points) if b["score"] > a["score"]]
    metrics: dict = {}
    if deltas:
        metrics["latest_pt"] = deltas[-1][1]
        last10 = deltas[-10:]
        metrics["average_round"] = len(last10)
        metrics["average_pt"] = sum(d for _, d in last10) // len(last10)
    end_ms = max(points[-1]["time"], now_ms)
    hour_pts = [p for p in points if p["time"] >= end_ms - 3_600_000]
    if len(hour_pts) >= 2 and hour_pts[-1]["time"] > hour_pts[0]["time"]:
        inc = hour_pts[-1]["score"] - hour_pts[0]["score"]
        actual_s = (hour_pts[-1]["time"] - hour_pts[0]["time"]) // 1000
        metrics["speed"] = inc * 3600 // actual_s
        metrics["hour_round"] = sum(1 for t, _ in deltas if t >= end_ms - 3_600_000)
    m20 = [p for p in points if p["time"] >= end_ms - 1_200_000]
    if len(m20) >= 2:
        metrics["min20_times_3_speed"] = (m20[-1]["score"] - m20[0]["score"]) * 3
    record_start = points[0]["time"]
    for (t0, _), (t1, _) in pairwise(deltas):
        if t1 - t0 >= 300_000:
            record_start = t1
    metrics["record_start_at"] = record_start
    return metrics


def tracked_rank_info(rank: int, name: str, rng: random.Random, *, now_ms: int = NOW_MS,
                      scale: float = 1.0, round_pt: tuple[int, int] | None = None) -> dict:
    """A single tracked entry with metrics derived from a synthetic recent trace."""
    if round_pt is None:
        # keep per-round points coherent with the rank's border pace (~2.8%/h of border)
        mid = max(int(border_now(rank, scale) * 0.0017), 1_200)
        round_pt = (int(mid * 0.78), int(mid * 1.22))
    start = now_ms - 4 * 3_600_000
    brk_end = now_ms - rng.randint(30, 90) * 60_000
    breaks = [(brk_end - rng.randint(6, 14) * 60_000, brk_end)]
    trace = synth_minute_trace(name, start, now_ms - rng.randint(20, 60) * 1000, breaks, rng,
                               round_pt=round_pt, rank_start=rank, rank_end=rank)
    target = int(border_now(rank, scale) * rng.uniform(0.995, 1.005))
    shift = target - trace[-1]["score"]
    for p in trace:
        p["score"] += shift
    last = trace[-1]
    return {"rank": rank, "name": name, "score": last["score"], "time": last["time"],
            **derive_metrics(trace, now_ms)}


def count_csb_stop_texts(points: list[dict]) -> int:
    """Mirror of the CSB drawer's stop-segment scan (src/sekai/sk/drawer.py:635-681)."""
    ranks = sorted(points, key=lambda p: p["time"])
    segments: list[tuple[dict, dict]] = []
    left = right = None
    for p in ranks:
        if left is None:
            left = p
        if right is None:
            right = p
        if p["rank"] > 100 or p["time"] - right["time"] > 70_000:
            if left is not right:
                segments.append((left, right))
            left, right = p, None
        elif p["score"] != right["score"]:
            if left is not right:
                segments.append((left, right))
            left, right = p, None
        else:
            right = p
    if left is not None and right is not None and left is not right:
        segments.append((left, right))
    return 1 + sum(1 for lo, hi in segments if hi["time"] - lo["time"] >= 300_000)


# ---------------------------------------------------------------------------
# Event meta + asset helpers (controller_meta.go:31-60, assets/helper.go:291-303)
# ---------------------------------------------------------------------------


def event_banner_path(assetbundle_name: str) -> str:
    return common.ASSETS.region_asset(
        f"home/banner/{assetbundle_name}/{assetbundle_name}.png",
        f"event/{assetbundle_name}/banner.png",
        f"event_story/{assetbundle_name}/screen_image/banner_event_story.png",
    )


def event_meta(event_id: int) -> dict:
    ev = common.MD.event_by_id()[event_id]
    return {
        "id": event_id,
        "name": ev.get("name") or f"Event #{event_id}",
        "start_at": ev["startAt"],
        "aggregate_at": ev["aggregateAt"],
        "banner": event_banner_path(ev["assetbundleName"]),
        "assetbundle_name": ev["assetbundleName"],
    }


def wl_chapter(event_id: int, cid: int) -> dict:
    for ch in common.MD.get("worldBlooms"):
        if ch["eventId"] == event_id and ch["gameCharacterId"] == cid:
            return ch
    raise KeyError(f"no world bloom chapter for event {event_id} cid {cid}")


def jst(y: int, mo: int, d: int, h: int, mi: int = 0) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=JST).timestamp() * 1000)


# ---------------------------------------------------------------------------
# sk endpoints
# ---------------------------------------------------------------------------


def gen_sk_line() -> dict:
    rng = random.Random(210_001)
    meta = event_meta(EVENT_ID)
    ranks = []
    for rank in DEFAULT_NORMAL_RANKS:
        if rank == 300_000:  # skipMissing: border not reached yet
            continue
        ranks.append({
            "rank": rank,
            "name": "",  # controller_line_requests.go:30-32
            "score": int(border_now(rank) * rng.uniform(0.99, 1.01)),
            "time": NOW_MS - rng.randint(0, 90) * 1000,
        })
    return {
        "id": meta["id"],
        "region": "jp",
        "start_at": meta["start_at"],
        "aggregate_at": meta["aggregate_at"],
        "name": meta["name"],
        "banner_img_path": meta["banner"],
        "ranks": ranks,
        "timezone": common.TIMEZONE,
        "dt": NOW_MS,
    }


def gen_sk_line_predict() -> dict:
    rng = random.Random(210_002)
    meta = event_meta(EVENT_ID)
    forecast_ranks = [100, 200, 300, 400, 500, 1000, 1500, 2000, 2500, 3000,
                      4000, 5000, 10000, 20000, 30000, 40000, 50000, 100000]
    final_mult = 2.6  # score at aggregate vs now (70h of 196h elapsed)

    def column(key: str, name: str, covered: list[int], bias: float, fetched_min_ago: int) -> dict:
        forecast_time = NOW_MS - fetched_min_ago * 60_000
        return {
            "key": key,
            "name": name,
            "ranks": [{
                "rank": rank,
                "name": "",
                "score": int(border_now(rank) * final_mult * bias * rng.uniform(0.985, 1.015)),
                "time": forecast_time,
            } for rank in covered],
            "forecast_time": forecast_time,
            "update_time": NOW_MS - 2 * 60_000,
        }

    current = [{
        "rank": rank,
        "name": "",
        "score": int(border_now(rank) * rng.uniform(0.99, 1.01)),
        "time": NOW_MS - rng.randint(0, 90) * 1000,
    } for rank in forecast_ranks]
    return {
        "id": meta["id"],
        "region": "jp",
        "start_at": meta["start_at"],
        "aggregate_at": meta["aggregate_at"],
        "name": f"{meta['name']} 预测",
        "banner_img_path": meta["banner"],
        "ranks": current,
        "current_ranks": current,
        "forecast_columns": [
            column("33kit", "33Kit预测", forecast_ranks, 1.0, 9),
            column("moesekai", "Moesekai预测", [r for r in forecast_ranks if r not in (1500, 2500)], 0.97, 14),
            column("sekarun", "SekaRun预测", [r for r in forecast_ranks if r <= 5000], 1.03, 21),
            column("local", "本地预测", forecast_ranks, 0.99, 5),
        ],
        "prediction_notice": "预测数据仅供参考，请以实际为准规划好冲榜计划",
        "timezone": common.TIMEZONE,
        "dt": NOW_MS,
    }


def gen_sk_query() -> dict:
    """WL-chapter query (event 207 ch.1): names kept, metrics for tracked top-100."""
    rng = random.Random(207_001)
    meta = event_meta(WL_EVENT_ID)
    chapter = wl_chapter(WL_EVENT_ID, WL_CHAPTER_CID)
    wl_now = chapter["chapterStartAt"] + 20 * 3_600_000
    names = rng.sample(PLAYER_NAMES, len(PLAYER_NAMES))
    ranks = []
    for i, rank in enumerate(DEFAULT_WL_RANKS):
        if rank in (70_000, 100_000):  # skipMissing on chapter day 1
            continue
        name = names[i % len(names)]
        if rank <= 100:
            ranks.append(tracked_rank_info(rank, name, rng, now_ms=wl_now, scale=0.16))
        else:
            ranks.append({
                "rank": rank,
                "name": name,
                "score": int(border_now(rank, 0.16) * rng.uniform(0.99, 1.01)),
                "time": wl_now - rng.randint(0, 120) * 1000,
            })
    icon = common.ASSETS.chara_icon(WL_CHAPTER_CID)
    return {
        "id": meta["id"],
        "region": "jp",
        "name": meta["name"],
        "aggregate_at": chapter["aggregateAt"],  # handler/sk.go:612-622 chapter override
        "ranks": ranks,
        "wl_chara_icon_path": icon,
        "chara_icon_path": icon,  # query_requests.go:108-114 (same value)
        "timezone": common.TIMEZONE,
        "dt": wl_now,
    }


def gen_sk_check_room() -> dict:
    """Classic /cf: single target room with tracker previous/next (rank±1)."""
    rng = random.Random(210_003)
    meta = event_meta(EVENT_ID)
    return {
        "eid": meta["id"],
        "event_name": meta["name"],
        "region": "jp",
        "ranks": [tracked_rank_info(26, "ミズキと添い遂げる会", rng)],
        "prev_rank": tracked_rank_info(25, "夜更かしカナデ", rng),
        "next_rank": tracked_rank_info(27, "翠のマグロ", rng),
        "aggregate_at": meta["aggregate_at"],
        "update_at": NOW_MS,  # query_requests.go:177
        "timezone": common.TIMEZONE,
        "dt": NOW_MS,
    }


def gen_sk_check_room_multi() -> dict:
    """/cfl: the default lite rank table, one room row per rank, no prev/next."""
    rng = random.Random(210_004)
    meta = event_meta(EVENT_ID)
    names = rng.sample(PLAYER_NAMES, len(CFL_RANKS))
    return {
        "eid": meta["id"],
        "event_name": meta["name"],
        "region": "jp",
        "ranks": [tracked_rank_info(rank, names[i], rng) for i, rank in enumerate(CFL_RANKS)],
        "aggregate_at": meta["aggregate_at"],
        "update_at": NOW_MS,
        "timezone": common.TIMEZONE,
        "dt": NOW_MS,
    }


def _csb_body(name: str, breaks: list[tuple[int, int]], seed: int, *,
              round_pt: tuple[int, int], rank_end: int) -> dict:
    rng = random.Random(seed)
    meta = event_meta(EVENT_ID)
    trace = synth_minute_trace(name, meta["start_at"], NOW_MS - 90_000, breaks, rng,
                               round_pt=round_pt, rank_start=88, rank_end=rank_end)
    trace.append({**trace[-1], "time": NOW_MS})  # idle point (metrics.go:137-152)
    return {
        "eid": meta["id"],
        "event_name": meta["name"],
        "region": "jp",
        "ranks": trace,
        "aggregate_at": meta["aggregate_at"],
        "update_at": NOW_MS,
        "timezone": common.TIMEZONE,
        "dt": NOW_MS,
    }


def gen_sk_csb() -> dict:
    # 5 parking windows (3 nights + 2 meals) -> stop_texts = 6 < 10 (scale 1.5 branch)
    breaks = [
        (jst(2026, 7, 10, 2, 30), jst(2026, 7, 10, 9, 10)),
        (jst(2026, 7, 10, 12, 40), jst(2026, 7, 10, 13, 20)),
        (jst(2026, 7, 11, 1, 50), jst(2026, 7, 11, 8, 30)),
        (jst(2026, 7, 11, 18, 5), jst(2026, 7, 11, 18, 45)),
        (jst(2026, 7, 12, 2, 10), jst(2026, 7, 12, 9, 0)),
    ]
    # ~48.5 play hours at ~16.7 rounds/h -> final score lands near the T21 border
    body = _csb_body("ろん@イベラン中", breaks, 210_005, round_pt=(9_500, 15_700), rank_end=21)
    texts = count_csb_stop_texts(body["ranks"])
    assert texts < 10, f"sk_csb expected <10 stop texts, got {texts}"
    return body


def gen_sk_csb_large() -> dict:
    # 3 nights + 11 short rests + parked-at-now tail -> stop_texts >= 10 (scale 1.0 branch)
    breaks = [
        (jst(2026, 7, 9, 18, 10), jst(2026, 7, 9, 18, 40)),
        (jst(2026, 7, 9, 21, 30), jst(2026, 7, 9, 21, 45)),
        (jst(2026, 7, 10, 2, 40), jst(2026, 7, 10, 8, 50)),
        (jst(2026, 7, 10, 12, 15), jst(2026, 7, 10, 12, 55)),
        (jst(2026, 7, 10, 16, 20), jst(2026, 7, 10, 16, 32)),
        (jst(2026, 7, 10, 19, 0), jst(2026, 7, 10, 19, 25)),
        (jst(2026, 7, 10, 23, 10), jst(2026, 7, 10, 23, 30)),
        (jst(2026, 7, 11, 2, 20), jst(2026, 7, 11, 9, 5)),
        (jst(2026, 7, 11, 12, 30), jst(2026, 7, 11, 13, 5)),
        (jst(2026, 7, 11, 15, 40), jst(2026, 7, 11, 15, 55)),
        (jst(2026, 7, 11, 19, 10), jst(2026, 7, 11, 19, 40)),
        (jst(2026, 7, 12, 1, 50), jst(2026, 7, 12, 8, 40)),
        (jst(2026, 7, 12, 10, 30), jst(2026, 7, 12, 10, 42)),
        (jst(2026, 7, 12, 12, 20), jst(2026, 7, 12, 14, 0)),  # parked through "now" (13:00 JST)
    ]
    body = _csb_body("全人類ネネロボ化計画", breaks, 210_006, round_pt=(9_000, 15_200), rank_end=30)
    texts = count_csb_stop_texts(body["ranks"])
    assert texts >= 10, f"sk_csb_large expected >=10 stop texts, got {texts}"
    return body


def _speed_body(request_type: str, period_s: int, gain_ratio: float, seed: int) -> dict:
    rng = random.Random(seed)
    meta = event_meta(EVENT_ID)
    ranks = []
    for rank in DEFAULT_NORMAL_RANKS:
        if rank == 300_000:
            continue
        speed = 0 if rank >= 200_000 else int(border_now(rank) * gain_ratio * rng.uniform(0.8, 1.2))
        ranks.append({
            "rank": rank,
            "score": int(border_now(rank) * rng.uniform(0.99, 1.01)),
            "speed": speed,  # nil -> 0 (controller_tracker_v2.go:203-234)
            "record_time": NOW_MS - rng.randint(0, 90) * 1000,
        })
    return {
        "event_id": meta["id"],
        "region": "jp",
        "event_name": meta["name"],
        "event_start_at": meta["start_at"],
        "event_aggregate_at": meta["aggregate_at"],
        "ranks": ranks,
        "is_wl_event": False,
        "request_type": request_type,
        "period": period_s,
        "banner_img_path": meta["banner"],
        "timezone": common.TIMEZONE,
        "dt": NOW_MS,
    }


def gen_sk_speed() -> dict:
    return _speed_body("时", 3600, 0.028, 210_007)


def gen_sk_speed_daily() -> dict:
    return _speed_body("日", 86_400, 0.34, 210_008)


def gen_sk_player_trace() -> dict:
    rng = random.Random(210_009)
    meta = event_meta(EVENT_ID)
    nights = [
        (jst(2026, 7, 10, 2, 0), jst(2026, 7, 10, 9, 0)),
        (jst(2026, 7, 11, 1, 30), jst(2026, 7, 11, 8, 30)),
        (jst(2026, 7, 12, 2, 30), jst(2026, 7, 12, 9, 30)),
    ]
    trace_a = synth_minute_trace("みのり号泣した", meta["start_at"], NOW_MS, nights, rng,
                                 step_ms=300_000, round_pt=(10_000, 16_200), rank_start=70, rank_end=18)
    nights_b = [(s + 3_600_000, e + 1_800_000) for s, e in nights]
    trace_b = synth_minute_trace("ツカサ様の下僕No.1", meta["start_at"], NOW_MS, nights_b, rng,
                                 step_ms=300_000, round_pt=(7_600, 12_400), rank_start=95, rank_end=41)
    compare_trace = [
        {"rank": 100, "name": "翠のマグロ", "score": score, "time": t}
        for t, score in border_series(100, meta["start_at"], NOW_MS, 900_000, rng)
    ]
    latest = compare_trace[-1]
    return {
        "event_id": meta["id"],
        "region": "jp",
        "ranks": trace_a,
        "ranks2": trace_b,
        "compare_rank": 100,
        "compare_rank_trace": compare_trace,
        "compare_rank_latest": latest,
        "compare_rank_line_score": latest["score"],
        "timezone": common.TIMEZONE,
        "dt": NOW_MS,
    }


def gen_sk_rank_trace() -> dict:
    rng = random.Random(210_010)
    meta = event_meta(EVENT_ID)
    holders = ["翠のマグロ", "エナドリ絵名", "Karin_pjsk", "月夜のセレナーデ", "白石杏推し二号",
               "シホの弦", "sleepy_mfy", "風鈴と司"]
    ranks = []
    holder, holder_until = holders[0], meta["start_at"]
    for t, score in border_series(100, meta["start_at"], NOW_MS, 300_000, rng):
        if t >= holder_until:
            holder = rng.choice(holders)
            holder_until = t + rng.randint(2, 12) * 3_600_000
        ranks.append({"rank": 100, "name": holder, "score": score, "time": t})
    return {
        "event_id": meta["id"],
        "region": "jp",
        "target_rank": 100,
        "ranks": ranks,
        "timezone": common.TIMEZONE,
        "dt": NOW_MS,
    }


def gen_sk_winrate() -> dict:
    """Cloud has no builder for this endpoint (handler/sk.go:515-519); synthesized
    per the Python model from cheerfulCarnivalTeams.json (spec §1.8)."""
    meta = event_meta(CC_EVENT_ID)
    teams = [t for t in common.MD.get("cheerfulCarnivalTeams") if t["eventId"] == CC_EVENT_ID]
    teams.sort(key=lambda t: t["seq"])
    cn_names = {77: "水族馆婚礼", 78: "天文馆婚礼"}
    win_rates = {77: 0.463, 78: 0.537}
    dt = meta["start_at"] + 5 * 86_400_000  # mid-event
    return {
        "event_id": meta["id"],
        "event_name": meta["name"],
        "region": "jp",
        "updated_at": dt - 20 * 60_000,
        "event_start_at": meta["start_at"],
        "event_aggregate_at": meta["aggregate_at"],
        "banner_img_path": meta["banner"],
        "team_info": [{
            "team_id": t["id"],
            "team_name": t["teamName"],
            "win_rate": win_rates[t["id"]],
            "is_recruiting": win_rates[t["id"]] < 0.5,
            "team_cn_name": cn_names[t["id"]],
            "team_icon_path": common.ASSETS.region_asset(
                f"event/{meta['assetbundle_name']}/team_image/{t['assetbundleName']}.png"
            ),
        } for t in teams],
        "timezone": common.TIMEZONE,
        "dt": dt,
    }


# ---------------------------------------------------------------------------
# misc endpoints
# ---------------------------------------------------------------------------

MUSIC_ALIASES = [
    "吸血鬼", "vampire", "ばんぱいあ", "凡派亚", "小吸血鬼", "吸血姬", "DECO吸血鬼", "香蕉皮",
    "vam", "ヴァンパイア", "深海少女2", "红裙", "红裙子", "吸吸", "血族", "德古拉", "吸血鬼之歌",
    "someok神曲", "维安帕亚", "苦无", "红色心电图", "爱人错误", "才不是什么吸血鬼",
]

CHARACTER_ALIASES = [
    "初音未来", "初音", "miku", "米库", "葱", "大葱", "葱娘", "世界第一公主殿下", "公主殿下",
    "hatsune miku", "ミク", "阿绿", "绿双马尾", "赛博歌姬", "电子歌姬", "虚拟歌姬一号",
    "初音ミク", "初音みく", "39", "米酷", "初音酱", "葱姐",
]


def gen_misc_alias_list() -> dict:
    music = next(m for m in common.MD.get("musics") if m["id"] == 213)  # ヴァンパイア
    ab = music["assetbundleName"]
    return {
        "title": "歌曲别名",
        "entity_label": "歌曲ID",
        "entity_id": music["id"],
        "entity_name": music["title"],
        "music_jacket_path": common.ASSETS.region_asset(f"music/jacket/{ab}/{ab}.png"),
        "aliases": MUSIC_ALIASES,
        "timezone": common.TIMEZONE,
        "dt": NOW_MS,
    }


def gen_misc_alias_list_character() -> dict:
    cid = 21
    # alias.go:418-424: region always empty -> jp-assets/startapp, no probing
    trim = common.ASSETS.region_asset(f"character/character_trim/chr_trim_{cid}.png")
    return {
        "title": "角色别名",
        "entity_label": "角色ID",
        "entity_id": cid,
        "entity_name": "初音ミク",
        "character_trim_path": trim,
        "character_silhouette_path": trim,  # same value (spec §2.1)
        "aliases": CHARACTER_ALIASES,
        "timezone": common.TIMEZONE,
        "dt": NOW_MS,
    }


# misc_birthday.go:57-84 (hardcoded, not from masterdata)
CHARACTER_BIRTHDAYS = {
    1: (8, 11), 2: (5, 9), 3: (10, 27), 4: (1, 8), 5: (4, 14), 6: (10, 5), 7: (3, 19),
    8: (12, 6), 9: (3, 2), 10: (7, 26), 11: (11, 12), 12: (5, 25), 13: (5, 17), 14: (9, 9),
    15: (7, 20), 16: (6, 24), 17: (2, 10), 18: (1, 27), 19: (4, 30), 20: (8, 27), 21: (8, 31),
    22: (12, 27), 23: (12, 27), 24: (1, 30), 25: (11, 5), 26: (2, 17),
}


def _next_birthday_ms(month: int, day: int, now: datetime) -> int:
    """birthday_helpers.go:179-187 (JP region, UTC+9)."""
    region_now = now.astimezone(JST)
    nxt = datetime(region_now.year, month, day, tzinfo=JST)
    if region_now >= nxt + timedelta(days=1):
        nxt = datetime(region_now.year + 1, month, day, tzinfo=JST)
    return int(nxt.timestamp() * 1000)


def _birthday_event_time(start_ms: int, end_ms: int) -> dict:
    display_end = end_ms - 60_000  # buildBirthdayEventTime: display end minus 1 minute
    if display_end < start_ms:
        display_end = end_ms
    return {"start_at": start_ms, "end_at": display_end}


def gen_misc_chara_birthday() -> dict:
    now = datetime.fromtimestamp(NOW_MS / 1000, CST)
    infos = sorted(
        ((cid, m, d, _next_birthday_ms(m, d, now)) for cid, (m, d) in CHARACTER_BIRTHDAYS.items()),
        key=lambda item: (item[3], item[0]),
    )
    cid, month, day, next_ms = infos[0]  # upcoming_index = 1
    bday_cards = sorted(
        (c for c in common.MD.get("cards")
         if c["characterId"] == cid and c["cardRarityType"] == "rarity_birthday"),
        key=lambda c: (c.get("releaseAt", 0), c["id"]),
    )
    cards = [{
        "id": c["id"],
        "thumbnail_path": common.ASSETS.region_asset(f"thumbnail/chara/{c['assetbundleName']}_normal.png"),
    } for c in bday_cards]
    card_image_path = common.ASSETS.region_asset(
        f"character/member/{bday_cards[-1]['assetbundleName']}/card_normal.png"
    )
    day_ms = 86_400_000
    days_until = max(int((next_ms - NOW_MS) // day_ms), 0) if next_ms > NOW_MS else 0
    return {
        "cid": cid,
        "month": month,
        "day": day,
        "region_name": "日服",
        "days_until_birthday": days_until,
        "color_code": common.MD.character_color_code().get(cid, "#FFFFFF"),
        "sd_image_path": common.ASSETS.region_asset(f"character/character_sd_l/chr_sp_{cid}.png"),
        "title_image_path": common.ASSETS.region_asset(f"character/label_horizontal/chr_h_lb_{cid}.png"),
        "card_image_path": card_image_path,
        "cards": cards,
        "is_fifth_anniv": True,  # JP only (misc_birthday.go:54-56)
        "gacha_time": _birthday_event_time(next_ms - 4 * day_ms, next_ms + 3 * day_ms),
        "live_time": _birthday_event_time(next_ms, next_ms + day_ms),
        "drop_time": _birthday_event_time(next_ms - 3 * day_ms, next_ms),
        "flower_time": _birthday_event_time(next_ms - 3 * day_ms, next_ms + 3 * day_ms),
        "party_time": _birthday_event_time(next_ms, next_ms + 3 * day_ms),
        "all_characters": [{
            "cid": icid,
            "month": im,
            "day": idy,
            "icon_path": common.ASSETS.chara_icon(icid),
        } for icid, im, idy, _ in infos],
        "timezone": common.TIMEZONE,
        "dt": NOW_MS,
    }


# ---------------------------------------------------------------------------
# help/render (command_help.go)
# ---------------------------------------------------------------------------

SK_QUERY_HELPDOC = """# SK 查询

## 用法
- `/sk [查询参数...]`

## 参数
- 查询条件：可写活动、排名、玩家、时间等 SK 查询条件。
- 指令别名：部分 SK 别名会进入相同查询入口。

## 输出
- 返回 SK 排名、档线或活动追踪信息。

## 示例
- `/sk`
- `/sk t100`"""

# handler/sk.go: every route registered with Path "sk/query"
SK_QUERY_COMMANDS = ["/sk-query", "/sk查询", "/sk查分", "/pjsk sk board", "/pjsk board", "/sk"]


def _with_alias_section(markdown: str, commands: list[str]) -> str:
    """command_help.go:211-243: missing aliases, sorted by rune length then lex, 4 per line."""
    missing = [c for c in dict.fromkeys(commands) if c not in markdown]
    missing.sort(key=lambda c: (len(c), c))
    if not missing:
        return markdown.strip()
    lines = ["- " + " ".join(f"`{alias}`" for alias in missing[i:i + 4]) for i in range(0, len(missing), 4)]
    return (markdown.strip() + "\n\n## 指令别名\n" + "\n".join(lines)).strip()


def gen_help_render() -> dict:
    markdown = _with_alias_section(SK_QUERY_HELPDOC, SK_QUERY_COMMANDS)
    return {
        "path": "sk/query",
        "title": "SK 查询",  # first '#' heading
        "markdown": markdown,
        "timezone": common.TIMEZONE,
        "dt": NOW_MS,
    }


# ---------------------------------------------------------------------------
# stamp/list
# ---------------------------------------------------------------------------

STAMP_PAGE_SIZE = 25
STAMP_PROMPT = "\n".join([
    '发送"/stamp 序号"获取单张表情',
    '发送"/stamp 序号 序号"获取多张表情',
    '发送"/stamp 角色名"按角色筛选表情',
    '发送"/stamp page 2"查看指定页',
    '发送"/stamp all"返回全部页',
])


def gen_stamp_list() -> dict:
    stamps = sorted(common.MD.get("stamps"), key=lambda s: s["id"])
    # Production drops stamps whose image is missing locally (controller.go:207-256); offline we
    # keep every stamp and let the fallback path land in the rsync manifest instead. Only the
    # page-1 slice goes through ASSETS so the manifest stays limited to shipped paths.
    total_pages = max(-(-len(stamps) // STAMP_PAGE_SIZE), 1)
    items = [{
        "id": s["id"],
        "image_path": common.ASSETS.region_asset(f"stamp/{s['assetbundleName']}/{s['assetbundleName']}.png"),
        "text_color": [200, 0, 0, 255],
    } for s in stamps[:STAMP_PAGE_SIZE]]
    return {
        "prompt_message": STAMP_PROMPT,
        "page_message": f"第 1 / {total_pages} 页",
        "stamps": items,
        "timezone": common.TIMEZONE,
        "dt": NOW_MS,
    }


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------

ENDPOINTS = [
    ("sk_line", SklRequest, gen_sk_line),
    ("sk_line_predict", SklRequest, gen_sk_line_predict),
    ("sk_query", SKRequest, gen_sk_query),
    ("sk_check_room", CFRequest, gen_sk_check_room),
    ("sk_check_room_multi", CFRequest, gen_sk_check_room_multi),
    ("sk_csb", CSBRequest, gen_sk_csb),
    ("sk_csb_large", CSBRequest, gen_sk_csb_large),
    ("sk_speed", SpeedRequest, gen_sk_speed),
    ("sk_speed_daily", SpeedRequest, gen_sk_speed_daily),
    ("sk_player_trace", PlayerTraceRequest, gen_sk_player_trace),
    ("sk_rank_trace", RankTraceRequest, gen_sk_rank_trace),
    ("sk_winrate", WinRateRequest, gen_sk_winrate),
    ("misc_alias_list", AliasListRequest, gen_misc_alias_list),
    ("misc_alias_list_character", AliasListRequest, gen_misc_alias_list_character),
    ("misc_chara_birthday", CharaBirthdayRequest, gen_misc_chara_birthday),
    ("help_render", CommandHelpRenderRequest, gen_help_render),
    ("stamp_list", StampListRequest, gen_stamp_list),
]


def generate() -> list[str]:
    written: list[str] = []
    for name, model, builder in ENDPOINTS:
        body = builder()
        model.model_validate(body)  # must pass the drawing-side request model
        common.write_payload(name, body)
        written.append(name)
    return written


if __name__ == "__main__":
    for payload_name in generate():
        print(payload_name)  # noqa: T201
    own_missing = len(common.ASSETS.missing)
    # The rsync manifest is shared across the per-domain generators and save_manifest()
    # overwrites it; merge in what sibling runs already recorded so no entries are lost.
    for attr, fname in (("used", "assets-used.txt"), ("missing", "assets-missing.txt")):
        manifest = common.OUT_DIR / fname
        if manifest.exists():
            getattr(common.ASSETS, attr).update(line for line in manifest.read_text().splitlines() if line)
    common.ASSETS.save_manifest()
    print(f"missing assets (this domain): {own_missing}")  # noqa: T201
