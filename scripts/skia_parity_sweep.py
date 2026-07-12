"""Skia-vs-Pillow parity sweep over the real request payloads in ``out/parity-payloads/``.

Successor of the sprint-era ``out/skia-parity-sweep/`` tooling: same mechanism
(in-process Skia gates forced on, composed caches bypassed, per-endpoint diff
stats + side-by-side PNG), but driven by the 58 real request bodies produced by
``scripts/parity_payloads/`` (all pre-validated against the drawing pydantic
models) and extended with per-path wall-clock timing.

For every payload the harness:
    pil     = await compose_X_image(req)            # Pillow ground truth (timed)
    payload = await try_render_X_payload(req)       # Skia shadow-layer path (timed)
    diff(pil, decode(payload)) -> mean/max/p99/p999 ; save <name>_sbs.png

Result rows: {endpoint, status, size_*, mean, max, p99, p999, elapsed_pillow,
elapsed_skia, sbs, note?, error?}. Statuses: ok / size-mismatch / skia-none /
pillow-only / pillow-none / pillow-error / skia-error / build-error /
harness-error / skipped / no-payload.

Known deviations (not failures):
- ``honor``: Skia is excluded by design -> Pillow baseline only.
- ``mysekai_*`` (except housing-competition): needs the gitignored
  ``src/sekai/mysekai/drawer.real.py``; the whole domain is ``skipped`` when absent.

CAVEAT: when local ``data/`` assets are incomplete, absolute pixel diffs mix
genuine renderer drift with missing-asset artifacts. Treat the numbers as
layout/structure parity indicators, not photometric truth.

Run (repo root):
    uv run python scripts/skia_parity_sweep.py [--only name1,name2] [--out-dir out/parity-sweep-real]
"""

from __future__ import annotations

import os

# Must be set before src.settings is imported.
os.environ.setdefault("HARUKI_DRAWING__USE_PROCESS_POOL", "false")

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
import importlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import sys
import time
import traceback

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
from PIL import Image, ImageChops

from src.settings import settings

PAYLOAD_DIR = REPO_ROOT / "out" / "parity-payloads"
DEFAULT_OUT_DIR = REPO_ROOT / "out" / "parity-sweep-real"

# Sentinel drawer module: resolved at runtime from the gitignored drawer.real.py.
MYSEKAI_REAL = "mysekai-real"

# Every cache getter a drawer module may consult before rebuilding. All are
# monkeypatched to return None so both paths are actually exercised and the
# timings are honest (composed LRU, composed disk cache, Skia payload cache).
_CACHE_GETTERS = ("get_composed_image_cached", "get_composed_image_disk_cached", "get_skia_payload_cached")

_KNOWN_BLOCKED_PREFIX = "known-blocked"

# Statuses that are expected/benign; anything else counts as a failure, except
# skia-none rows explicitly annotated as known-blocked.
_OK_STATUSES = {"ok", "pillow-only", "skipped", "no-payload"}


@dataclass(frozen=True)
class Case:
    """One payload -> (request model, Pillow compose, Skia try_render) binding."""

    name: str
    drawer: str
    compose: str
    model_module: str
    model_cls: str
    try_render: str | None
    try_render_module: str | None = None  # defaults to `drawer`
    is_list: bool = False
    note: str | None = None


def _case(
    name: str,
    domain: str,
    stem: str,
    model_cls: str,
    *,
    try_render: str | None = "",
    try_render_module: str | None = None,
    drawer: str | None = None,
    is_list: bool = False,
    note: str | None = None,
) -> Case:
    return Case(
        name=name,
        drawer=drawer or f"src.sekai.{domain}.drawer",
        compose=f"compose_{stem}_image",
        model_module=f"src.sekai.{domain}.model",
        model_cls=model_cls,
        try_render=f"try_render_{stem}_payload" if try_render == "" else try_render,
        try_render_module=try_render_module,
        is_list=is_list,
        note=note,
    )


_SKIA_CARD_RENDER = "src.sekai.skia_renderer.card_render"

CASES: tuple[Case, ...] = (
    # ---- card ----
    _case("card_detail", "card", "card_detail", "CardDetailRequest"),
    _case("card_list", "card", "card_list", "CardListRequest", try_render_module=_SKIA_CARD_RENDER),
    _case("card_box", "card", "box", "CardBoxRequest", try_render="try_render_box_payload"),
    # ---- chart (crate renders the chart body; the watermark shell is the migrated part) ----
    _case("chart", "chart", "music_chart", "GenerateMusicChartRequest"),
    # ---- costume ----
    _case("costume_list", "costume", "costume_list", "CostumeListRequest"),
    _case("costume_detail", "costume", "costume_detail", "CostumeDetailRequest"),
    # ---- deck (direct in-process call, no heavy worker) ----
    _case("deck_recommend", "deck", "deck_recommend", "DeckRequest"),
    # ---- education ----
    _case("education_challenge_live", "education", "challenge_live_detail", "ChallengeLiveDetailsRequest"),
    _case("education_power_bonus", "education", "power_bonus_detail", "PowerBonusDetailRequest"),
    _case("education_area_item", "education", "area_item_upgrade_materials", "AreaItemUpgradeMaterialsRequest"),
    _case("education_bonds", "education", "bonds", "BondsRequest"),
    _case("education_leader_count", "education", "leader_count", "LeaderCountRequest"),
    _case(
        "education_character_mission_overview",
        "education",
        "character_mission_overview",
        "CharacterMissionOverviewRequest",
    ),
    _case("education_character_mission_all", "education", "character_mission_all", "CharacterMissionAllRequest"),
    # ---- event (event_planner delegates to the deck path internally) ----
    _case("event_list", "event", "event_list", "EventListRequest"),
    _case("event_detail", "event", "event_detail", "EventDetailRequest"),
    _case("event_record", "event", "event_record", "EventRecordRequest"),
    _case("event_planner", "event", "event_planner", "EventPlannerRequest"),
    # ---- gacha ----
    _case("gacha_list", "gacha", "gacha_list", "GachaListRequest"),
    _case("gacha_detail", "gacha", "gacha_detail", "GachaDetailRequest"),
    # ---- honor (Skia excluded by design -> Pillow baseline only) ----
    _case("honor", "honor", "full_honor", "HonorRequest", try_render=None, note="pillow-only by design"),
    # ---- inventory ----
    _case("inventory_list", "inventory", "inventory_list", "InventoryListRequest"),
    # ---- misc (chara_birthday is a direct in-process call, no heavy worker) ----
    _case("misc_alias_list", "misc", "alias_list", "AliasListRequest"),
    _case("misc_alias_list_character", "misc", "alias_list", "AliasListRequest"),
    _case("misc_chara_birthday", "misc", "chara_birthday", "CharaBirthdayRequest"),
    _case("help_render", "misc", "command_help", "CommandHelpRenderRequest"),
    # ---- music ----
    _case("music_detail", "music", "music_detail", "MusicDetailRequest"),
    _case("music_brief_list", "music", "music_brief_list", "MusicBriefListRequest"),
    _case("music_list", "music", "music_list", "MusicListRequest"),
    _case("music_progress", "music", "play_progress", "PlayProgressRequest"),
    _case("music_rewards_detail", "music", "detail_music_rewards", "DetailMusicRewardsRequest"),
    _case("music_rewards_basic", "music", "basic_music_rewards", "BasicMusicRewardsRequest"),
    # ---- mysekai (drawer.real.py; housing-competition lives in the public housing_drawer) ----
    _case("mysekai_resource", "mysekai", "mysekai_resource", "MysekaiResourceRequest", drawer=MYSEKAI_REAL),
    _case("mysekai_map", "mysekai", "mysekai_msr_map", "MysekaiMsrMapRequest", drawer=MYSEKAI_REAL),
    _case("mysekai_fixture_list", "mysekai", "mysekai_fixture_list", "MysekaiFixtureListRequest", drawer=MYSEKAI_REAL),
    _case(
        "mysekai_fixture_detail",
        "mysekai",
        "mysekai_fixture_detail",
        "MysekaiFixtureDetailRequest",
        drawer=MYSEKAI_REAL,
        is_list=True,  # request body is a JSON array -> list[MysekaiFixtureDetailRequest]
    ),
    _case("mysekai_door_upgrade", "mysekai", "mysekai_door_upgrade", "MysekaiDoorUpgradeRequest", drawer=MYSEKAI_REAL),
    _case("mysekai_music_record", "mysekai", "mysekai_musicrecord", "MysekaiMusicrecordRequest", drawer=MYSEKAI_REAL),
    _case("mysekai_talk_list", "mysekai", "mysekai_talk_list", "MysekaiTalkListRequest", drawer=MYSEKAI_REAL),
    _case(
        "mysekai_housing_competition",
        "mysekai",
        "mysekai_housing_competition",
        "MysekaiHousingCompetitionRequest",
        drawer="src.sekai.mysekai.housing_drawer",
    ),
    # ---- profile ----
    _case("profile", "profile", "profile", "ProfileRequest"),
    # ---- score ----
    _case("score_control", "score", "score_control", "ScoreControlRequest"),
    _case("score_custom_room", "score", "custom_room_score_control", "CustomRoomScoreRequest"),
    _case("score_music_meta", "score", "music_meta", "MusicMetaRequest", is_list=True),
    _case("score_music_board", "score", "music_board", "MusicBoardRequest"),
    # ---- sk (csb build returns (canvas, scale); handled inside compose/try_render) ----
    _case("sk_line", "sk", "skl", "SklRequest"),
    _case("sk_line_predict", "sk", "skl", "SklRequest"),
    _case("sk_query", "sk", "sk", "SKRequest"),
    _case("sk_check_room", "sk", "cf", "CFRequest"),
    _case("sk_check_room_multi", "sk", "cf", "CFRequest"),
    _case("sk_csb", "sk", "csb", "CSBRequest"),
    _case("sk_csb_large", "sk", "csb", "CSBRequest"),
    _case("sk_speed", "sk", "sks", "SpeedRequest"),
    _case("sk_speed_daily", "sk", "sks", "SpeedRequest"),
    _case("sk_player_trace", "sk", "player_trace", "PlayerTraceRequest"),
    _case("sk_rank_trace", "sk", "rank_trace", "RankTraceRequest"),
    _case("sk_winrate", "sk", "winrate_predict", "WinRateRequest"),
    # ---- stamp ----
    _case("stamp_list", "stamp", "stamp_list", "StampListRequest", try_render="try_render_stamp_payload"),
    # ---- vlive ----
    _case("vlive_list", "vlive", "vlive_list", "VLiveListRequest"),
)


# ---------------------------------------------------------------------------
# Setup / cache bypass (mechanism inherited from out/skia-parity-sweep/_sweep_common.py)
# ---------------------------------------------------------------------------


def setup() -> None:
    """Disable the process pool and force every Skia gate on. Call once at start."""
    settings.drawing.use_process_pool = False
    for flag in ("use_skia_plot", "use_skia_card_list"):
        if hasattr(settings.drawing, flag):
            setattr(settings.drawing, flag, True)


def bypass_caches(*modules) -> None:
    """Neutralize every composed/disk/Skia-payload cache getter on the given modules
    so compose() and try_render() always rebuild and the timings are honest."""
    for mod in modules:
        if mod is None:
            continue
        for attr in _CACHE_GETTERS:
            if hasattr(mod, attr):
                setattr(mod, attr, lambda *a, **k: None)


# ---------------------------------------------------------------------------
# Diff / side-by-side helpers
# ---------------------------------------------------------------------------


def _to_rgb(img: Image.Image) -> Image.Image:
    return img.convert("RGB") if img.mode != "RGB" else img


def _diff_stats(a: Image.Image, b: Image.Image) -> dict:
    a, b = _to_rgb(a), _to_rgb(b)
    if a.size != b.size:
        return {"size_match": False, "size_pil": list(a.size), "size_skia": list(b.size)}
    d = np.asarray(ImageChops.difference(a, b), dtype=np.float32)
    return {
        "size_match": True,
        "size_pil": list(a.size),
        "size_skia": list(b.size),
        "mean": round(float(d.mean()), 3),
        "max": round(float(d.max()), 1),
        "p99": round(float(np.percentile(d, 99)), 1),
        "p999": round(float(np.percentile(d, 99.9)), 1),
    }


def _save_sbs(out_dir: Path, name: str, pil: Image.Image, skia: Image.Image) -> str:
    pil, skia = _to_rgb(pil), _to_rgb(skia)
    w = max(pil.width, skia.width)
    h = max(pil.height, skia.height)
    sbs = Image.new("RGB", (w * 2 + 10, h), (40, 40, 40))
    sbs.paste(pil, (0, 0))
    sbs.paste(skia, (w + 10, 0))
    path = out_dir / f"{name}_sbs.png"
    sbs.save(path)
    return path.name


# ---------------------------------------------------------------------------
# Payload / model / module resolution
# ---------------------------------------------------------------------------


def _load_payload(name: str):
    path = PAYLOAD_DIR / f"{name}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _build_model(model_cls, raw, is_list: bool):
    if is_list:
        items = raw if isinstance(raw, list) else [raw]
        return [model_cls.model_validate(item) for item in items]
    if isinstance(raw, list):  # single-model endpoint dumped as a 1-element list
        raw = raw[0]
    return model_cls.model_validate(raw)


def _load_mysekai_real():
    """Load the proprietary drawer.real.py under the real package name so its
    relative imports (``from .model import ...``) resolve. Returns None when absent."""
    path = REPO_ROOT / "src" / "sekai" / "mysekai" / "drawer.real.py"
    if not path.exists():
        return None
    mod_name = "src.sekai.mysekai._drawer_real_parity"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "src.sekai.mysekai"
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Case runner
# ---------------------------------------------------------------------------


async def run_case(case: Case, req, compose, try_render, out_dir: Path) -> dict:
    """Run one endpoint through both paths (each individually timed) and return a row."""
    row: dict = {"endpoint": case.name}
    if case.note:
        row["note"] = case.note

    # Pillow ground truth
    t0 = time.perf_counter()
    try:
        pil = await compose(req)
    except Exception as exc:
        row["status"] = "pillow-error"
        row["elapsed_pillow"] = round(time.perf_counter() - t0, 3)
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["trace"] = traceback.format_exc(limit=4)
        return row
    row["elapsed_pillow"] = round(time.perf_counter() - t0, 3)
    if pil is None:
        row["status"] = "pillow-none"
        return row
    row["size_pil"] = list(_to_rgb(pil).size)

    # Skia shadow-layer path (absent for pillow-only endpoints such as honor)
    if try_render is None:
        row["status"] = "pillow-only"
        pillow_png = out_dir / f"{case.name}_pillow.png"
        _to_rgb(pil).save(pillow_png)
        row["pillow_png"] = pillow_png.name
        return row
    t0 = time.perf_counter()
    try:
        payload = await try_render(req)
    except Exception as exc:
        row["status"] = "skia-error"
        row["elapsed_skia"] = round(time.perf_counter() - t0, 3)
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["trace"] = traceback.format_exc(limit=6)
        return row
    row["elapsed_skia"] = round(time.perf_counter() - t0, 3)
    if payload is None:
        row["status"] = "skia-none"  # gate off / unsupported op / known-blocked fence
        return row

    skia = Image.open(BytesIO(payload.image_bytes))
    stats = _diff_stats(pil, skia)
    row.update(stats)
    row["status"] = "ok" if stats.get("size_match") else "size-mismatch"
    row["sbs"] = _save_sbs(out_dir, case.name, pil, skia)
    return row


async def _run_one(case: Case, mysekai_real, out_dir: Path) -> dict:
    raw = _load_payload(case.name)
    if raw is None:
        return {"endpoint": case.name, "status": "no-payload"}
    if case.drawer == MYSEKAI_REAL and mysekai_real is None:
        return {"endpoint": case.name, "status": "skipped", "note": "drawer.real.py not present locally"}
    try:
        drawer = mysekai_real if case.drawer == MYSEKAI_REAL else importlib.import_module(case.drawer)
        tr_mod = importlib.import_module(case.try_render_module) if case.try_render_module else drawer
        bypass_caches(drawer, tr_mod)
        compose = getattr(drawer, case.compose)
        try_render = getattr(tr_mod, case.try_render) if case.try_render else None
        model_cls = getattr(importlib.import_module(case.model_module), case.model_cls)
        req = _build_model(model_cls, raw, case.is_list)
    except Exception as exc:
        return {
            "endpoint": case.name,
            "status": "build-error",
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(limit=4),
        }
    try:
        return await run_case(case, req, compose, try_render, out_dir)
    except Exception as exc:
        return {
            "endpoint": case.name,
            "status": "harness-error",
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(limit=6),
        }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _is_failure(row: dict) -> bool:
    status = row.get("status", "")
    if status in _OK_STATUSES:
        return False
    if status == "skia-none" and str(row.get("note", "")).startswith(_KNOWN_BLOCKED_PREFIX):
        return False
    return True


def _fmt(value, spec: str = "") -> str:
    if value is None:
        return "-"
    return format(value, spec) if spec else str(value)


def _summary_group_key(row: dict) -> str:
    status = row.get("status", "?")
    if status == "skia-none" and str(row.get("note", "")).startswith(_KNOWN_BLOCKED_PREFIX):
        return "skia-none (known-blocked)"
    return status


_STATUS_ORDER = (
    "ok",
    "size-mismatch",
    "skia-none",
    "skia-none (known-blocked)",
    "pillow-only",
    "pillow-none",
    "skia-error",
    "pillow-error",
    "build-error",
    "harness-error",
    "skipped",
    "no-payload",
)


def write_summary_md(rows: list[dict], out_dir: Path) -> Path:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(_summary_group_key(row), []).append(row)

    failures = [r for r in rows if _is_failure(r)]
    lines = [
        "# Skia parity sweep (real payloads)",
        "",
        f"Payload dir: `{PAYLOAD_DIR.relative_to(REPO_ROOT)}` | cases: {len(rows)} | failures: {len(failures)}",
        "",
        "known-blocked / pillow-only / skipped rows are expected deviations, not failures.",
        "",
    ]
    order = [s for s in _STATUS_ORDER if s in grouped] + [s for s in grouped if s not in _STATUS_ORDER]
    for status in order:
        group = grouped[status]
        lines.append(f"## {status} ({len(group)})")
        lines.append("")
        lines.append("| endpoint | size | mean | p99 | pillow_s | skia_s | speedup | note |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for row in sorted(group, key=lambda r: r["endpoint"]):
            size = "x".join(str(v) for v in row["size_pil"]) if row.get("size_pil") else "-"
            if row.get("size_skia") and row.get("size_pil") != row.get("size_skia"):
                size += " / " + "x".join(str(v) for v in row["size_skia"])
            ep, es = row.get("elapsed_pillow"), row.get("elapsed_skia")
            speedup = f"{ep / es:.2f}x" if ep and es else "-"
            note = row.get("note") or row.get("error") or ""
            lines.append(
                f"| {row['endpoint']} | {size} | {_fmt(row.get('mean'))} | {_fmt(row.get('p99'))} "
                f"| {_fmt(ep)} | {_fmt(es)} | {speedup} | {note} |"
            )
        lines.append("")
    path = out_dir / "SUMMARY.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def sweep(only: set[str] | None, out_dir: Path, mysekai_real) -> list[dict]:
    """Serial on purpose: keeps the two per-case wall-clock timings uncontended."""
    rows: list[dict] = []
    for case in CASES:
        if only is not None and case.name not in only:
            continue
        row = await _run_one(case, mysekai_real, out_dir)
        rows.append(row)
        print(  # noqa: T201
            f"{row.get('status', '?'):<12} {case.name:<38} mean={_fmt(row.get('mean')):<8} "
            f"pillow={_fmt(row.get('elapsed_pillow'))}s skia={_fmt(row.get('elapsed_skia'))}s "
            f"{row.get('error', '')}"
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="comma-separated payload names to run (default: all)")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="output directory for results/SBS images")
    args = parser.parse_args()

    only: set[str] | None = None
    if args.only:
        only = {name.strip() for name in args.only.split(",") if name.strip()}
        unknown = only - {c.name for c in CASES}
        if unknown:
            parser.error(f"unknown case name(s): {', '.join(sorted(unknown))}")

    setup()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    on_disk = {p.stem for p in PAYLOAD_DIR.glob("*.json")}
    for unmapped in sorted(on_disk - {c.name for c in CASES}):
        print(f"[warn] payload without a case mapping: {unmapped}")  # noqa: T201

    try:
        mysekai_real = _load_mysekai_real()
    except Exception as exc:
        print(f"[warn] mysekai drawer.real.py failed to load: {type(exc).__name__}: {exc}")  # noqa: T201
        mysekai_real = None
    if mysekai_real is not None:
        bypass_caches(mysekai_real)

    rows = asyncio.run(sweep(only, out_dir, mysekai_real))

    results_path = out_dir / "results.json"
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump({"payload_dir": str(PAYLOAD_DIR), "cases": rows}, fh, ensure_ascii=False, indent=2)
    summary_path = write_summary_md(rows, out_dir)

    counts = Counter(_summary_group_key(r) for r in rows)
    failures = sum(1 for r in rows if _is_failure(r))
    print(f"\n=== status counts === {dict(counts)}")  # noqa: T201
    print(f"failures (excluding known-blocked/pillow-only/skipped): {failures}")  # noqa: T201
    print(f"results: {results_path}\nsummary: {summary_path}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
