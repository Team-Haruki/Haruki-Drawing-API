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

from scripts.skia_parity_sweep import CASES, MYSEKAI_REAL, PAYLOAD_DIR, _load_payload

# The baseline renders in a worktree that has no copy of the untracked config/assets.
_UNTRACKED_NEEDED = ("configs.yaml",)

# configs.yaml points assets at a RELATIVE ./data, and data/ is gitignored (859MB of untracked
# art), so a fresh worktree has none of it -- every image would resolve to the missing-image
# placeholder and EVERY case would report drift, at every ref, including HEAD against itself.
# Link the real asset tree in. (The harness self-check --ref HEAD must come back all-ok; if it
# does not, the harness is measuring itself, not the code.)
_ASSET_DIRS = ("data",)

# Rendered with a fixed clock so a day/night background does not diff against itself.
_RENDER_ENV = {"HARUKI_BG_TEST_HOUR": "12.0"}

# The CURRENT tree does not need this: since d562865 the triangle scatter is generated once, in
# Python (base/triangle_bg.py, seeded off the quantized hour), and both backends draw that same
# list -- neither has a PRNG any more. But this harness renders the BASELINE ref too, and on an
# older ref Pillow still scattered from the UNSEEDED global `random`, so two renders of the same
# tree there differ by ~12% of pixels on their own. Seeding the global RNG keeps the baseline side
# reproducible; on the current side it is a harmless no-op.
_RNG_SEED = 12345

# Baseline comparisons intentionally support only the stable branch and the current
# checkout.  Mapping a CLI choice to literals prevents revision expressions or
# option-like strings from ever crossing the git command boundary.
_BASELINE_REFS = {"main": "main", "HEAD": "HEAD"}

_BASELINE_DRIVER = textwrap.dedent(
    """
    import asyncio, importlib, json, random, sys
    from pathlib import Path

    payload_path, drawer_mod, model_mod, model_cls, compose_fn, is_list, seed, out_png = sys.argv[1:9]

    async def main():
        random.seed(int(seed))
        mod = importlib.import_module(drawer_mod)
        cls = getattr(importlib.import_module(model_mod), model_cls)
        raw = json.loads(Path(payload_path).read_text())
        req = [cls(**item) for item in raw] if is_list == "1" else cls(**raw)
        image = await getattr(mod, compose_fn)(req)
        image.save(out_png)

    asyncio.run(main())
    """
).strip()


def _prepare_worktree(ref: str, workdir: Path) -> Path:
    ref = _BASELINE_REFS[ref]
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

    for name in _ASSET_DIRS:
        src = REPO_ROOT / name
        dst = tree / name
        if not src.exists():
            continue
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
        elif dst.is_dir():
            shutil.rmtree(dst)
        dst.symlink_to(src, target_is_directory=True)

    # Turn the process pool OFF in the BASELINE's config (a no-op on refs at or after its removal,
    # but older baselines still ship `use_process_pool: true`). It has to be the yaml, not a
    # HARUKI_ env var: the "env beats yaml" precedence fix only exists on this branch, so on an
    # older baseline the yaml wins and the env var is ignored. A spawned worker in the throwaway
    # worktree cannot import the tree it was launched from (BrokenProcessPool); the thread pool
    # renders the same pixels anyway.
    config = tree / "configs.yaml"
    if config.exists():
        config.write_text(config.read_text().replace("use_process_pool: true", "use_process_pool: false"))
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
            "1" if case.is_list else "0",
            str(_RNG_SEED),
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


def _exists_on_baseline(tree: Path, case) -> bool:
    """Whether the baseline tree even has this drawer + model + compose function."""
    probe = (
        "import importlib,sys\n"
        f"m=importlib.import_module({case.drawer!r})\n"
        f"mm=importlib.import_module({case.model_module!r})\n"
        f"sys.exit(0 if hasattr(m,{case.compose!r}) and hasattr(mm,{case.model_cls!r}) else 3)\n"
    )
    import os

    proc = subprocess.run(
        [sys.executable, "-X", "gil=0", "-c", probe],
        cwd=tree,
        env={**os.environ, "PYTHONPATH": str(tree)},
        capture_output=True,
    )
    return proc.returncode == 0


async def _render_current(case, raw) -> Image.Image:
    import importlib
    import random

    random.seed(_RNG_SEED)
    mod = importlib.import_module(case.drawer)
    cls = getattr(importlib.import_module(case.model_module), case.model_cls)
    req = [cls(**item) for item in raw] if case.is_list else cls(**raw)
    return await getattr(mod, case.compose)(req)


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


def _parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--ref",
        choices=tuple(_BASELINE_REFS),
        default="main",
        help="baseline git ref (default: main)",
    )
    ap.add_argument("--only", default="", help="comma-separated case names")
    ap.add_argument("--tolerance", type=int, default=0, help="max per-channel delta tolerated")
    ap.add_argument("--out-dir", default="out/legacy-baseline")
    return ap.parse_args()


def _preflight_case(tree: Path, case, payload_file: Path) -> dict | None:
    if not payload_file.exists():
        return {"case": case.name, "status": "no-payload"}
    if case.drawer == MYSEKAI_REAL:
        # drawer.real.py is gitignored, so the baseline worktree only has the stub.
        return {"case": case.name, "status": "no-baseline", "detail": "mysekai drawer.real.py"}
    if not _exists_on_baseline(tree, case):
        # The endpoint did not exist on the baseline ref — nothing to drift from.
        return {"case": case.name, "status": "no-baseline", "detail": "endpoint is new"}
    return None


def _comparison_status(stats: dict, tolerance: int) -> str:
    if not stats.get("size_match"):
        return "size-mismatch"
    return "drift" if stats["max_delta"] > tolerance else "ok"


def _print_case_result(case, row: dict, stats: dict) -> None:
    detail = (
        f"max_delta={stats.get('max_delta')} differing={stats.get('differing_pct')}%"
        if stats.get("size_match")
        else f"{stats.get('size_a')} vs {stats.get('size_b')}"
    )
    print(f"{row['status']:<15} {case.name:<28} {detail}")  # noqa: T201


def _run_case(tree: Path, case, out_dir: Path, env_extra: dict, tolerance: int) -> dict:
    payload_file = PAYLOAD_DIR / f"{case.name}.json"
    preflight = _preflight_case(tree, case, payload_file)
    if preflight is not None:
        return preflight

    row: dict = {"case": case.name}
    base_png = out_dir / f"{case.name}_baseline.png"
    error = _render_baseline(tree, case, payload_file, base_png, env_extra)
    if error:
        row.update(status="baseline-error", detail=error)
        return row
    try:
        current = asyncio.run(_render_current(case, _load_payload(case.name)))
    except Exception as exc:
        row.update(status="current-error", detail=f"{type(exc).__name__}: {exc}")
        return row

    current.save(out_dir / f"{case.name}_current.png")
    stats = _diff(Image.open(base_png), current)
    row.update(stats)
    row["status"] = _comparison_status(stats, tolerance)
    _print_case_result(case, row, stats)
    return row


def _cleanup_worktree(tree: Path) -> None:
    subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=REPO_ROOT, check=False)
    subprocess.run(["git", "worktree", "prune"], cwd=REPO_ROOT, check=False)


def _run_cases(ref: str, cases: list, out_dir: Path, env_extra: dict, tolerance: int) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        tree = _prepare_worktree(ref, Path(tmp))
        try:
            return [_run_case(tree, case, out_dir, env_extra, tolerance) for case in cases]
        finally:
            _cleanup_worktree(tree)


def _report_results(ref: str, out_dir: Path, rows: list[dict]) -> bool:
    (out_dir / "results.json").write_text(json.dumps({"ref": ref, "cases": rows}, indent=2, ensure_ascii=False))
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print(f"\n=== status counts === {counts}")  # noqa: T201
    print(f"results: {out_dir / 'results.json'}")  # noqa: T201
    drift = [row for row in rows if row["status"] in ("drift", "size-mismatch")]
    if drift:
        print("\nDRIFT vs baseline (both backends would render this wrong — the parity sweep cannot see it):")  # noqa: T201
        for row in drift:
            print(f"  {row['case']}: {row}")  # noqa: T201
    return bool(drift)


def main() -> int:
    args = _parse_args()

    wanted = {n.strip() for n in args.only.split(",") if n.strip()}
    cases = [c for c in CASES if not wanted or c.name in wanted]
    out_dir = REPO_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    env_extra = dict(_RENDER_ENV)
    import os

    os.environ.update(env_extra)
    rows = _run_cases(args.ref, cases, out_dir, env_extra, args.tolerance)
    return 1 if _report_results(args.ref, out_dir, rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
