"""Run fresh cold/native-service and warm-cache checks before publishing a native release.

Requires Linux, CPython 3.14t, the current extension, dev reference dependencies,
configured assets, and out/parity-payloads. The output directory must be new so an
interrupted run cannot accidentally reuse an older passing report.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def run_step(command: list[str], log: Path, timeout: int) -> int:
    with log.open("w") as output:
        process = subprocess.Popen(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return 124


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "out/release-gate")
    parser.add_argument("--timeout", type=int, default=5400, help="Maximum seconds per phase")
    args = parser.parse_args()
    if platform.system() != "Linux" or getattr(sys, "_is_gil_enabled", lambda: True)():
        parser.error("release validation requires Linux CPython free-threading with the GIL disabled")
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    destination = args.out_dir.resolve()
    if destination.exists():
        parser.error("output directory already exists; choose a new directory for this release run")
    destination.mkdir(parents=True)
    commands = {
        "cold": ["scripts/skia_parity_sweep.py", "--strict", "--save-images"],
        "warm": ["scripts/skia_warm_parity.py", "--strict", "--backend", "both"],
    }
    result = {"platform": platform.platform(), "python": sys.version, "phases": {}}
    try:
        git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True)
        result["git_revision"] = git.stdout.strip() if git.returncode == 0 else None
    except OSError:
        # Runtime/reference images need no git executable; image provenance is recorded by the caller.
        result["git_revision"] = None
    for phase, command in commands.items():
        print(f"Starting {phase} release validation; log: {destination / (phase + '.log')}", flush=True)  # noqa: T201
        started = time.monotonic()
        code = run_step(
            [sys.executable, "-X", "gil=0", *command, "--out-dir", str(destination / phase)],
            destination / f"{phase}.log",
            args.timeout,
        )
        report = destination / phase / "results.json"
        result["phases"][phase] = {
            "exit_code": code,
            "report_exists": report.is_file(),
            "seconds": time.monotonic() - started,
        }
        # Both phases run for diagnostics even if the first fails. Only new successful
        # executions with reports can authorize the downstream image publishing job.
        (destination / "release.json").write_text(json.dumps(result, indent=2))
    result["passed"] = all(row["exit_code"] == 0 and row["report_exists"] for row in result["phases"].values())
    (destination / "release.json").write_text(json.dumps(result, indent=2))
    print(f"Release validation passed: {result['passed']}", flush=True)  # noqa: T201
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
