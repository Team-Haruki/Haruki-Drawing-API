"""Warm-cache parity: does a cache HIT return the same image a cold render would?

`skia_parity_sweep.py` -- the only pixel-level gate this repo has -- calls `bypass_caches()` and
**deliberately turns every composed / disk / Skia-payload cache off** so its timings are honest.
So the 63/63 it reports says "re-rendering from scratch is correct". It says *nothing* about the
path production actually takes, which is a cache hit. A key that omits something the output depends
on serves a different-but-perfectly-valid image, and nothing anywhere errors.

This harness renders with the caches ON and asks whether they lie.

    determinism  render twice, caches cleared each time. If those two disagree the case is
                 content-nondeterministic (live countdowns: `time_to_end = event_end - now`) and is
                 excluded -- otherwise it would masquerade as a cache defect.
    cold         caches cleared, render, hash. This is ground truth.
    warm-fwd     every case rendered in order, caches left hot. Each case's hash must equal cold.
    warm-rev     every case rendered AGAIN, reverse order. Must still equal cold.

The reverse pass is the point. A forward-only re-render mostly re-hits a case's *own* entries; going
backwards makes each case run against a cache filled by 62 *other* pages, which is what production
looks like and what catches:

  * a key collision      -- two payloads, one key, second request served the first one's pixels
  * mutation-on-hit      -- these caches hand back PIL.Image BY REFERENCE; one caller that pastes
                            onto a cached image corrupts the entry for every later request
  * cross-endpoint bleed -- the Rust raster cache is one shared pool for the whole process

Both backends are checked: Pillow has the image/thumb/resize/composed caches, Skia adds the payload
cache and the native Moka raster cache.

WHAT THIS DOES NOT COVER -- do not read a green run as more than it is:
  * Cases excluded as nondeterministic draw a live countdown, and WHICH ones get excluded varies
    between runs: it depends on whether the clock happened to tick during that run. (One run
    dropped all twelve sk_* plus gacha_detail and event_planner; the next compared every sk_* and
    passed them, leaving only the two whose content moves by the second.) So a green run does not
    mean a fixed set was checked. The countdown endpoints were verified by hand instead, across a
    deliberate 70s wait: the content moves with the clock, the warm render moves with it, and a
    warm render equals a cold render taken at the same instant -- nothing is frozen. That was a
    one-off. Pinning `now` behind a test hook would let this harness cover them properly.
  * One payload per endpoint means a KEY COLLISION between two different payloads cannot show up
    here. That has to come from reading the key material, not from this sweep.
  * Nothing on disk is mutated, so asset-staleness (an asset file edited under a live cache) is
    untested.

Run (repo root):
    uv run python -X gil=0 scripts/skia_warm_parity.py [--only a,b] [--backend skia|pillow|both]
"""

from __future__ import annotations

import os

# Same clock pin as the sweep: the triangle palette follows the fractional hour by design, so two
# renders seconds apart differ in colour for reasons that have nothing to do with caching.
os.environ.setdefault("HARUKI_BG_TEST_HOUR", "12.0")

import argparse
import asyncio
import hashlib
import importlib
from io import BytesIO
import json
from pathlib import Path
import sys
import traceback

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PIL import Image

from scripts.skia_parity_sweep import (
    CASES,
    MYSEKAI_REAL,
    Case,
    _build_model,
    _load_mysekai_real,
    _load_payload,
    setup,
)
from src.sekai.base import utils as base_utils
from src.sekai.skia_renderer.payload_cache import clear_skia_payload_cache

OUT_DIR = REPO_ROOT / "out" / "warm-parity"


def clear_all_caches() -> None:
    """Empty every layer a render can hit -- WITHOUT tearing down the thread pool the way
    ``shutdown_utils()`` does, because we have to keep rendering afterwards."""
    with base_utils._image_cache_lock:
        base_utils._image_cache.clear()
        base_utils._image_cache_total_bytes = 0
    with base_utils._thumb_cache_lock:
        base_utils._thumb_cache.clear()
        base_utils._thumb_cache_total_bytes = 0
    base_utils._composed_image_cache.clear()
    base_utils._load_asset_image_ref_cached.cache_clear()
    clear_skia_payload_cache()

    # The native Moka raster cache lives in the Rust process, not in any Python dict.
    try:
        from src.sekai.skia_renderer.canvas import load_native_renderer

        native = load_native_renderer()
        if native is not None and hasattr(native, "clear_renderer_caches"):
            native.clear_renderer_caches()
    except Exception:
        pass


def _hash_image(img: Image.Image) -> str:
    rgba = img.convert("RGBA")
    return hashlib.sha256(rgba.tobytes()).hexdigest()[:16] + f":{rgba.size[0]}x{rgba.size[1]}"


async def _render(case: Case, req, drawer, tr_mod, backend: str) -> str | None:
    """Render one case and return a pixel hash. None when this backend has no path for it."""
    if backend == "skia":
        if not case.try_render:
            return None
        try_render = getattr(tr_mod, case.try_render)
        payload = await try_render(req)
        if payload is None:
            return None  # fell open to Pillow
        return _hash_image(Image.open(BytesIO(payload.image_bytes)))

    compose = getattr(drawer, case.compose)
    img = await compose(req)
    if isinstance(img, tuple):  # sk csb returns (canvas, scale)
        img = img[0]
    return _hash_image(img)


def _bind(case: Case, mysekai_real):
    """Import the drawer and build the request model. NOTE: no bypass_caches() -- that is the point."""
    raw = _load_payload(case.name)
    if raw is None:
        return None, "no-payload"
    if case.drawer == MYSEKAI_REAL and mysekai_real is None:
        return None, "skipped"
    drawer = mysekai_real if case.drawer == MYSEKAI_REAL else importlib.import_module(case.drawer)
    tr_mod = importlib.import_module(case.try_render_module) if case.try_render_module else drawer
    model_cls = getattr(importlib.import_module(case.model_module), case.model_cls)
    req = _build_model(model_cls, raw, case.is_list)
    return (case, req, drawer, tr_mod), None


async def run(cases: list[Case], backend: str, mysekai_real) -> list[dict]:
    bound = []
    rows: dict[str, dict] = {}
    for case in cases:
        b, why = _bind(case, mysekai_real)
        if b is None:
            rows[case.name] = {"endpoint": case.name, "backend": backend, "status": why}
            continue
        bound.append(b)
        rows[case.name] = {"endpoint": case.name, "backend": backend}

    # --- determinism + cold reference: caches cleared before EVERY render ---
    for case, req, drawer, tr_mod in bound:
        row = rows[case.name]
        try:
            clear_all_caches()
            a = await _render(case, req, drawer, tr_mod, backend)
            clear_all_caches()
            b = await _render(case, req, drawer, tr_mod, backend)
        except Exception as exc:
            row.update(status="error", error=f"{type(exc).__name__}: {exc}", trace=traceback.format_exc(limit=4))
            continue
        if a is None:
            row["status"] = "no-path"
            continue
        row["cold"] = a
        if a != b:
            # Content moves on its own (live countdown). A cache can't be blamed for this.
            row["status"] = "nondeterministic"
            continue
        row["status"] = "pending"

    live = [b for b in bound if rows[b[0].name].get("status") == "pending"]

    # --- warm passes: caches stay hot across every case, forward then backward ---
    for label, order in (("warm_fwd", live), ("warm_rev", list(reversed(live)))):
        for case, req, drawer, tr_mod in order:
            row = rows[case.name]
            try:
                row[label] = await _render(case, req, drawer, tr_mod, backend)
            except Exception as exc:
                row.update(status="error", error=f"{type(exc).__name__}: {exc}")

    # --- cold again, at the END of the run ---
    # The two cold renders above are back-to-back, so anything that moves on a clock coarser than a
    # few milliseconds (an event countdown recomputed from `now`) looks perfectly deterministic to
    # them and then "drifts" during the warm passes minutes later -- a cache defect that is really a
    # clock. Re-render cold once more, now, at the far end of the run: if THAT disagrees with the
    # first cold render, the content is time-dependent and the caches are exonerated.
    for case, req, drawer, tr_mod in live:
        row = rows[case.name]
        if row.get("status") == "error":
            continue
        try:
            clear_all_caches()
            row["cold_after"] = await _render(case, req, drawer, tr_mod, backend)
        except Exception as exc:
            row.update(status="error", error=f"{type(exc).__name__}: {exc}")

    for case, _req, _d, _t in live:
        row = rows[case.name]
        if row.get("status") == "error":
            continue
        cold, fwd, rev, after = row.get("cold"), row.get("warm_fwd"), row.get("warm_rev"), row.get("cold_after")
        if cold == fwd == rev:
            row["status"] = "ok"
        elif cold != after:
            row["status"] = "nondeterministic"  # moved with the clock, not with the cache
            row["note"] = "two cold renders minutes apart disagree — time-dependent content"
        else:
            # Cold is reproducible across the whole run, yet a warm render disagrees with it.
            # The only thing that changed is the state of the caches.
            row["status"] = "CACHE-DRIFT"
            row["drift"] = {
                "cold_vs_warm_fwd": "same" if cold == fwd else "DIFFERENT",
                "cold_vs_warm_rev": "same" if cold == rev else "DIFFERENT",
                "warm_fwd_vs_warm_rev": "same" if fwd == rev else "DIFFERENT",
            }
    return list(rows.values())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--backend", default="both", choices=("skia", "pillow", "both"))
    args = ap.parse_args()

    setup()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mysekai_real = _load_mysekai_real()

    names = {n.strip() for n in args.only.split(",") if n.strip()}
    cases = [c for c in CASES if not names or c.name in names]
    backends = ("skia", "pillow") if args.backend == "both" else (args.backend,)

    all_rows: list[dict] = []
    for backend in backends:
        rows = asyncio.run(run(cases, backend, mysekai_real))
        all_rows += rows
        drift = [r for r in rows if r["status"] == "CACHE-DRIFT"]
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        print(f"\n=== {backend} === {counts}")  # noqa: T201
        for r in drift:
            print(f"  CACHE-DRIFT {r['endpoint']}: {r['drift']}")  # noqa: T201
            print(f"      cold={r.get('cold')}\n      fwd ={r.get('warm_fwd')}\n      rev ={r.get('warm_rev')}")  # noqa: T201
        for r in rows:
            if r["status"] == "error":
                print(f"  ERROR {r['endpoint']}: {r.get('error')}")  # noqa: T201

    results = OUT_DIR / "results.json"
    results.write_text(json.dumps({"cases": all_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = sum(1 for r in all_rows if r["status"] in ("CACHE-DRIFT", "error"))
    print(f"\nresults: {results}")  # noqa: T201
    print(f"CACHE-DRIFT + errors: {failures}")  # noqa: T201
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
