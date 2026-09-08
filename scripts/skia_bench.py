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
    uv run python -X gil=0 scripts/skia_bench.py --format jpg --jpg-quality 90
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
from src.sekai.honor import skia as honor_skia
from src.sekai.skia_renderer import canvas as skia_canvas
from src.sekai.skia_renderer.payload_cache import clear_skia_payload_cache
from src.settings import EXPORT_IMAGE_FORMAT, JPG_QUALITY

OUT = REPO_ROOT / "out" / "skia-bench"


async def bench_case(
    case,
    req,
    drawer,
    tr_mod,
    *,
    reps: int,
    cold: bool,
    output_format: str,
    jpg_quality: int,
) -> dict | None:
    expected_media_type = "image/jpeg" if output_format == "jpg" else "image/png"
    pillow_size = None

    async def pillow_bytes() -> tuple[float, int]:
        """compose + the encode the route would do — the whole cost of a Pillow response."""
        nonlocal pillow_size
        if cold:
            clear_all_caches()
        t0 = time.perf_counter()
        img = await getattr(drawer, case.compose)(req)
        if isinstance(img, tuple):  # sk csb returns (canvas, scale)
            img = img[0]
        if case.route_watermark:
            from src.sekai.base.draw import add_request_watermark_to_image

            img = await add_request_watermark_to_image(img, req)
        pillow_size = img.size
        encoded, _, _ = _encode_image(img, output_format, jpg_quality)
        return time.perf_counter() - t0, encoded.getbuffer().nbytes

    async def skia_bytes() -> tuple[float, int] | None:
        if cold:
            clear_all_caches()
        # honor is the one endpoint left with a payload cache, and repeating the same request would
        # HIT it -- reporting 0.05ms and a fake 22x, which is a cache lookup, not a render. (It
        # would not hit in production either: honor's key folds in the watermark text, and that
        # carries dt to the SECOND.) Empty it so this measures the render, which is what it claims.
        clear_skia_payload_cache()
        t0 = time.perf_counter()
        payload = await getattr(tr_mod, case.try_render)(req)
        if payload is None:
            return None
        # Some routes intentionally pin PNG (chart, custom profile, command help). They are not
        # JPG encoder samples and must not silently contaminate a --format jpg result set.
        if payload.media_type != expected_media_type:
            return None
        if pillow_size is not None and pillow_size != (payload.image_width, payload.image_height):
            raise ValueError(
                f"response dimensions differ: Pillow {pillow_size}, Skia {payload.image_width}x{payload.image_height}"
            )
        return time.perf_counter() - t0, len(payload.image_bytes)

    if not case.try_render:
        return None
    if not cold:  # warm both paths first; production never renders into an empty cache twice
        await pillow_bytes()
        if await skia_bytes() is None:
            return None

    p_samples, s_samples = [], []
    for i in range(reps):
        # alternate, so neither backend systematically warms the OS page cache for the other
        for backend in ("pillow", "skia") if i % 2 == 0 else ("skia", "pillow"):
            sample = await (pillow_bytes() if backend == "pillow" else skia_bytes())
            if sample is None:
                return None
            (p_samples if backend == "pillow" else s_samples).append(sample)

    p, p_bytes = min(p_samples, key=lambda sample: sample[0])
    s, s_bytes = min(s_samples, key=lambda sample: sample[0])
    return {
        "endpoint": case.name,
        "format": output_format,
        "jpg_quality": jpg_quality if output_format == "jpg" else None,
        "pillow": p,
        "skia": s,
        "speedup": p / s,
        "pillow_bytes": p_bytes,
        "skia_bytes": s_bytes,
        "size_ratio": s_bytes / p_bytes,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cold", action="store_true", help="clear every cache before every render")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--format", choices=("configured", "png", "jpg"), default="configured")
    ap.add_argument("--jpg-quality", type=int, default=JPG_QUALITY)
    args = ap.parse_args()

    output_format = EXPORT_IMAGE_FORMAT if args.format == "configured" else args.format
    jpg_quality = max(1, min(100, args.jpg_quality))
    # Standard widget-tree routes read these module globals when building their IR. Honor has a
    # small hand-built watermark shell and imports the values separately, so update both. Routes
    # that deliberately pin PNG are detected by media type and excluded above.
    skia_canvas.EXPORT_IMAGE_FORMAT = output_format
    skia_canvas.JPG_QUALITY = jpg_quality
    honor_skia.EXPORT_IMAGE_FORMAT = output_format
    honor_skia.JPG_QUALITY = jpg_quality

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
            row = await bench_case(
                case,
                *bound[1:],
                reps=args.reps,
                cold=args.cold,
                output_format=output_format,
                jpg_quality=jpg_quality,
            )
        except Exception as exc:
            print(f"  {case.name:30s} ERROR {type(exc).__name__}: {exc}")  # noqa: T201
            continue
        if row is None:
            continue
        rows.append(row)
        print(  # noqa: T201
            f"  {row['endpoint']:30s} pillow {row['pillow'] * 1000:7.1f}ms   "
            f"skia {row['skia'] * 1000:7.1f}ms   {row['speedup']:5.2f}x   "
            f"size {row['pillow_bytes'] / 1024:7.1f}/{row['skia_bytes'] / 1024:7.1f} KiB"
        )

    if not rows:
        print("no cases benchmarked")  # noqa: T201
        return 1

    output_dir = OUT / output_format
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")

    tp = sum(r["pillow"] for r in rows)
    ts = sum(r["skia"] for r in rows)
    sp = [r["speedup"] for r in rows]
    slower = [r["endpoint"] for r in rows if r["speedup"] < 1.0]
    mode = "COLD (every cache cleared)" if args.cold else "WARM (steady state)"
    format_label = f"{output_format.upper()} quality={jpg_quality}" if output_format == "jpg" else output_format.upper()
    print(  # noqa: T201
        f"\n=== {len(rows)} cases, {mode}, {format_label}, both sides producing response bytes, min of {args.reps}"
    )
    print(f"  total    pillow {tp:6.2f}s   skia {ts:6.2f}s   -> {tp / ts:.2f}x")  # noqa: T201
    print(  # noqa: T201
        f"  median   pillow {statistics.median(r['pillow'] for r in rows) * 1000:5.0f}ms  "
        f"skia {statistics.median(r['skia'] for r in rows) * 1000:5.0f}ms"
    )
    print(f"  speedup  median {statistics.median(sp):.2f}x   best {max(sp):.2f}x   worst {min(sp):.2f}x")  # noqa: T201
    print(  # noqa: T201
        f"  bytes    pillow {sum(r['pillow_bytes'] for r in rows) / 1024 / 1024:.2f} MiB   "
        f"skia {sum(r['skia_bytes'] for r in rows) / 1024 / 1024:.2f} MiB"
    )
    print(f"  Skia slower on: {len(slower)}{' -> ' + str(slower) if slower else ''}")  # noqa: T201
    print(f"  results: {output_dir / 'results.json'}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
