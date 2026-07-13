"""Pixel-diff the CURRENT Pillow output against a BASELINE ref's Pillow output.

Why this exists, separately from ``skia_parity_sweep.py``:

    The parity sweep compares Pillow against Skia *on the current tree*. It is therefore blind
    to any drift both backends share — which is exactly what happens when a Pillow composer is
    ported to Painter primitives so both backends can draw it. ``CardFullThumbnailBox`` shipped
    with the level label 4px too high and translucent overlay edges, and the sweep stayed 63/63
    green the whole time, because BOTH backends drew the same wrong tree.

    This harness renders the same payload with the same Pillow path on the current branch and on
    a baseline ref (default: main, i.e. the pre-migration composers) and diffs the two images.

The baseline runs in a throwaway git worktree, in a subprocess, using only APIs that exist on
both trees (``compose_<x>_image(request)``), so no baseline-side code is needed.

    uv run python -X gil=0 scripts/skia_legacy_baseline.py
    uv run python -X gil=0 scripts/skia_legacy_baseline.py --only profile,card_list --ref main
    uv run python -X gil=0 scripts/skia_legacy_baseline.py --tolerance 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap

from PIL import Image, ImageChops

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.skia_parity_sweep import CASES, PAYLOAD_DIR, _load_payload

# The baseline renders in a worktree that has no copy of the untracked config/assets.
_UNTRACKED_NEEDED = ("configs.yaml",)

# Rendered with a fixed clock so a day/night background does not diff against itself.
# The process pool is off because a spawned worker in the baseline worktree cannot import the
# tree it was launched from (BrokenProcessPool); the thread pool renders the same pixels.
_RENDER_ENV = {
    "HARUKI_BG_TEST_HOUR": "12.0",
    "HARUKI_DRAWING__USE_PROCESS_POOL": "false",
}

_BASELINE_DRIVER = textwrap.dedent(
    """
    import asyncio, importlib, json, sys
    from pathlib import Path

    payload_path, drawer_mod, model_mod, model_cls, compose_fn, out_png = sys.argv[1:7]

    async def main():
        mod = importlib.import_module(drawer_mod)
        cls = getattr(importlib.import_module(model_mod), model_cls)
        req = cls(**json.loads(Path(payload_path).read_text()))
        image = await getattr(mod, compose_fn)(req)
        image.save(out_png)

    asyncio.run(main())
    """
).strip()


def _prepare_worktree(ref: str, workdir: Path) -> Path:
    tree = workdir / "baseline"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(tree), ref],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    for name in _UNTRACKED_NEEDED:
        src = REPO_ROOT / name
        if src.exists():
            shutil.copy2(src, tree / name)
    return tree


def _render_baseline(tree: Path, case, payload_path: Path, out_png: Path, env_extra: dict) -> str | None:
    """Render one case on the baseline tree. Returns an error string, or None on success."""
    driver = tree / "_baseline_driver.py"
    driver.write_text(_BASELINE_DRIVER)
    import os

    env = {**os.environ, **env_extra, "PYTHONPATH": str(tree)}
    proc = subprocess.run(
        [
            sys.executable,
            "-X",
            "gil=0",
            str(driver),
            str(payload_path),
            case.drawer,
            case.model_module,
            case.model_cls,
            case.compose,
            str(out_png),
        ],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()
        return tail[-1] if tail else f"exit {proc.returncode}"
    return None


async def _render_current(case, raw: dict) -> Image.Image:
    import importlib

    mod = importlib.import_module(case.drawer)
    cls = getattr(importlib.import_module(case.model_module), case.model_cls)
    return await getattr(mod, case.compose)(cls(**raw))


def _diff(a: Image.Image, b: Image.Image) -> dict:
    if a.size != b.size:
        return {"size_a": a.size, "size_b": b.size, "size_match": False}
    diff = ImageChops.difference(a.convert("RGBA"), b.convert("RGBA"))
    px = list(diff.get_flattened_data())
    total = len(px)
    # NOTE: not diff.getbbox() — getbbox keys off ALPHA, and the difference of two opaque
    # renders has alpha 0 everywhere, so it reports "identical" no matter how far the RGB drifts.
    worst = 0
    differing = 0
    channel_sum = 0
    for p in px:
        m = max(p)
        if m:
            differing += 1
            worst = max(worst, m)
        channel_sum += sum(p)
    return {
        "size_match": True,
        "differing_px": differing,
        "differing_pct": round(100.0 * differing / max(1, total), 3),
        "max_delta": worst,
        "mean_delta": round(channel_sum / max(1, total * 4), 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="main", help="baseline git ref (default: main)")
    ap.add_argument("--only", default="", help="comma-separated case names")
    ap.add_argument("--tolerance", type=int, default=0, help="max per-channel delta tolerated")
    ap.add_argument("--out-dir", default="out/legacy-baseline")
    args = ap.parse_args()

    wanted = {n.strip() for n in args.only.split(",") if n.strip()}
    cases = [c for c in CASES if not wanted or c.name in wanted]
    out_dir = REPO_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    env_extra = dict(_RENDER_ENV)
    import os

    os.environ.update(env_extra)

    rows: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        tree = _prepare_worktree(args.ref, Path(tmp))
        try:
            for case in cases:
                payload_file = PAYLOAD_DIR / f"{case.name}.json"
                if not payload_file.exists():
                    rows.append({"case": case.name, "status": "no-payload"})
                    continue
                row: dict = {"case": case.name}
                base_png = out_dir / f"{case.name}_baseline.png"
                err = _render_baseline(tree, case, payload_file, base_png, env_extra)
                if err:
                    row.update(status="baseline-error", detail=err)
                    rows.append(row)
                    continue
                try:
                    cur = asyncio.run(_render_current(case, _load_payload(case.name)))
                except Exception as exc:
                    row.update(status="current-error", detail=f"{type(exc).__name__}: {exc}")
                    rows.append(row)
                    continue
                cur_png = out_dir / f"{case.name}_current.png"
                cur.save(cur_png)

                stats = _diff(Image.open(base_png), cur)
                row.update(stats)
                if not stats.get("size_match"):
                    row["status"] = "size-mismatch"
                elif stats["max_delta"] > args.tolerance:
                    row["status"] = "drift"
                else:
                    row["status"] = "ok"
                rows.append(row)
                print(  # noqa: T201
                    f"{row['status']:<15} {case.name:<28} "
                    + (
                        f"max_delta={stats.get('max_delta')} differing={stats.get('differing_pct')}%"
                        if stats.get("size_match")
                        else f"{stats.get('size_a')} vs {stats.get('size_b')}"
                    )
                )
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=REPO_ROOT, check=False)
            subprocess.run(["git", "worktree", "prune"], cwd=REPO_ROOT, check=False)

    (out_dir / "results.json").write_text(json.dumps({"ref": args.ref, "cases": rows}, indent=2, ensure_ascii=False))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(f"\n=== status counts === {counts}")  # noqa: T201
    print(f"results: {out_dir / 'results.json'}")  # noqa: T201
    drift = [r for r in rows if r["status"] in ("drift", "size-mismatch")]
    if drift:
        print("\nDRIFT vs baseline (both backends would render this wrong — the parity sweep cannot see it):")  # noqa: T201
        for r in drift:
            print(f"  {r['case']}: {r}")  # noqa: T201
    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
