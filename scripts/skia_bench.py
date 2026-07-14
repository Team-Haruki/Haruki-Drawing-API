"""Pillow vs Skia, measured so the number means something.

Every timing this migration ever quoted came out of `skia_parity_sweep.py`, and it was wrong twice,
in opposite directions:

  * **Pillow warmed the cache for Skia.** The sweep renders Pillow first and does not bypass the
    image/thumb/resize decode caches, so Pillow paid for every cold decode and Skia inherited a hot
    one. `mysekai_music_record` read as **10.39x** when it is really **1.12x**.

  * **Only Skia was charged for the encode.** `compose_*_image()` returns a `PIL.Image`;
    `try_render_*_payload()` returns *encoded bytes*. The route encodes the Pillow image afterwards
    (`image_to_response`), but the sweep never timed that. So Skia carried a PNG encode that Pillow
    did not — which invented six endpoints where "Skia is slower". They do not exist:

        mysekai_map   pillow 36.5ms raster + 110.2ms encode = 146.7ms   vs   skia 44.5ms
        honor         pillow  0.1ms raster +   1.3ms encode =   1.4ms   vs   skia  0.1ms

    Pillow's PNG encoder is the hidden cost of the Pillow path: 19% of its total time across the
    63 cases. Skia does the whole render *and* the encode in one native pass.

So: both sides produce RESPONSE BYTES, both start warm (the state a live process is in), min of N.

    cold   every cache cleared before every render — first-request latency
    warm   caches hot — steady state, which is what production runs in (default)

Run (repo root):
    uv run python -X gil=0 scripts/skia_bench.py [--cold] [--reps 3] [--only a,b]
"""

from __future__ import annotations

import os

os.environ.setdefault("HARUKI_BG_TEST_HOUR", "12.0")

import argparse
import asyncio
import json
from pathlib import Path
import statistics
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.skia_parity_sweep import CASES, _load_mysekai_real, setup
from scripts.skia_warm_parity import _bind, clear_all_caches
from src.core.utils import _encode_image
from src.settings import EXPORT_IMAGE_FORMAT, JPG_QUALITY

OUT = REPO_ROOT / "out" / "skia-bench"


async def bench_case(case, req, drawer, tr_mod, *, reps: int, cold: bool) -> dict | None:
    async def pillow_bytes() -> float:
        """compose + the encode the route would do — the whole cost of a Pillow response."""
        if cold:
            clear_all_caches()
        t0 = time.perf_counter()
        img = await getattr(drawer, case.compose)(req)
        if isinstance(img, tuple):  # sk csb returns (canvas, scale)
            img = img[0]
        _encode_image(img, EXPORT_IMAGE_FORMAT, JPG_QUALITY)
        return time.perf_counter() - t0

    async def skia_bytes() -> float | None:
        if cold:
            clear_all_caches()
        t0 = time.perf_counter()
        payload = await getattr(tr_mod, case.try_render)(req)
        if payload is None:
            return None
        return time.perf_counter() - t0

    if not case.try_render:
        return None
    if not cold:  # warm both paths first; production never renders into an empty cache twice
        await pillow_bytes()
        if await skia_bytes() is None:
            return None

    p_times, s_times = [], []
    for i in range(reps):
        # alternate, so neither backend systematically warms the OS page cache for the other
        for backend in ("pillow", "skia") if i % 2 == 0 else ("skia", "pillow"):
            t = await (pillow_bytes() if backend == "pillow" else skia_bytes())
            if t is None:
                return None
            (p_times if backend == "pillow" else s_times).append(t)

    p, s = min(p_times), min(s_times)
    return {"endpoint": case.name, "pillow": p, "skia": s, "speedup": p / s}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cold", action="store_true", help="clear every cache before every render")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    setup()
    mysekai_real = _load_mysekai_real()
    names = {n.strip() for n in args.only.split(",") if n.strip()}
    clear_all_caches()

    rows = []
    for case in CASES:
        if names and case.name not in names:
            continue
        bound, _why = _bind(case, mysekai_real)
        if bound is None:
            continue
        try:
            row = await bench_case(case, *bound[1:], reps=args.reps, cold=args.cold)
        except Exception as exc:
            print(f"  {case.name:30s} ERROR {type(exc).__name__}: {exc}")  # noqa: T201
            continue
        if row is None:
            continue
        rows.append(row)
        print(  # noqa: T201
            f"  {row['endpoint']:30s} pillow {row['pillow'] * 1000:7.1f}ms   "
            f"skia {row['skia'] * 1000:7.1f}ms   {row['speedup']:5.2f}x"
        )

    if not rows:
        print("no cases benchmarked")  # noqa: T201
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")

    tp = sum(r["pillow"] for r in rows)
    ts = sum(r["skia"] for r in rows)
    sp = [r["speedup"] for r in rows]
    slower = [r["endpoint"] for r in rows if r["speedup"] < 1.0]
    mode = "COLD (every cache cleared)" if args.cold else "WARM (steady state)"
    print(f"\n=== {len(rows)} cases, {mode}, both sides producing response bytes, min of {args.reps}")  # noqa: T201
    print(f"  total    pillow {tp:6.2f}s   skia {ts:6.2f}s   -> {tp / ts:.2f}x")  # noqa: T201
    print(  # noqa: T201
        f"  median   pillow {statistics.median(r['pillow'] for r in rows) * 1000:5.0f}ms  "
        f"skia {statistics.median(r['skia'] for r in rows) * 1000:5.0f}ms"
    )
    print(f"  speedup  median {statistics.median(sp):.2f}x   best {max(sp):.2f}x   worst {min(sp):.2f}x")  # noqa: T201
    print(f"  Skia slower on: {len(slower)}{' -> ' + str(slower) if slower else ''}")  # noqa: T201
    print(f"  results: {OUT / 'results.json'}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
