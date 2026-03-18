#!/usr/bin/env python3
"""
Run drawing benchmarks under the current runtime mode (GIL on/off).

Use this script twice for comparison:
  python -X gil=1 scripts/benchmark_gil_compare.py --output out/bench-gil.json
  python -X gil=0 scripts/benchmark_gil_compare.py --output out/bench-nogil.json
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import math
from pathlib import Path
import statistics
import sys
import time
import warnings

from src.sekai.sk.drawer import compose_player_trace_image, compose_sk_image
from src.sekai.sk.model import PlayerTraceRequest, SKRequest

warnings.filterwarnings("ignore", message="Glyph .* missing from font")


@dataclass(slots=True)
class CaseConfig:
    name: str
    total: int
    concurrency: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run drawing benchmark for current GIL mode.")
    parser.add_argument(
        "--sk-payload",
        default="out/ci-sk-trend/sk_query_payload.json",
        help="Path to SK query payload JSON.",
    )
    parser.add_argument(
        "--trace-payload",
        default="out/ci-sk-trend/sk_player_trace_payload.json",
        help="Path to player trace payload JSON.",
    )
    parser.add_argument(
        "--output",
        default="out/bench-gil-current.json",
        help="Output JSON path.",
    )
    return parser.parse_args()


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    values = sorted(values)
    pos = (len(values) - 1) * p
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return values[low]
    ratio = pos - low
    return values[low] * (1 - ratio) + values[high] * ratio


async def run_case_sk(payload: dict, cfg: CaseConfig) -> dict:
    sem = asyncio.Semaphore(cfg.concurrency)
    lat = []
    fail = 0

    async def one() -> None:
        nonlocal fail
        async with sem:
            t0 = time.perf_counter()
            try:
                req = SKRequest.model_validate(payload)
                img = await compose_sk_image(req)
                img.close()
                lat.append((time.perf_counter() - t0) * 1000)
            except Exception:
                fail += 1

    t_start = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(cfg.total)))
    cost = time.perf_counter() - t_start
    return {
        "name": cfg.name,
        "total": cfg.total,
        "concurrency": cfg.concurrency,
        "ok": cfg.total - fail,
        "fail": fail,
        "throughput_rps": round(cfg.total / cost, 2),
        "latency_ms": {
            "avg": round(statistics.mean(lat), 2) if lat else 0.0,
            "p50": round(percentile(lat, 0.50), 2),
            "p95": round(percentile(lat, 0.95), 2),
            "p99": round(percentile(lat, 0.99), 2),
            "max": round(max(lat), 2) if lat else 0.0,
        },
        "elapsed_sec": round(cost, 3),
    }


async def run_case_trace(payload: dict, cfg: CaseConfig) -> dict:
    sem = asyncio.Semaphore(cfg.concurrency)
    lat = []
    fail = 0

    async def one() -> None:
        nonlocal fail
        async with sem:
            t0 = time.perf_counter()
            try:
                req = PlayerTraceRequest.model_validate(payload)
                img = await compose_player_trace_image(req)
                img.close()
                lat.append((time.perf_counter() - t0) * 1000)
            except Exception:
                fail += 1

    t_start = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(cfg.total)))
    cost = time.perf_counter() - t_start
    return {
        "name": cfg.name,
        "total": cfg.total,
        "concurrency": cfg.concurrency,
        "ok": cfg.total - fail,
        "fail": fail,
        "throughput_rps": round(cfg.total / cost, 2),
        "latency_ms": {
            "avg": round(statistics.mean(lat), 2) if lat else 0.0,
            "p50": round(percentile(lat, 0.50), 2),
            "p95": round(percentile(lat, 0.95), 2),
            "p99": round(percentile(lat, 0.99), 2),
            "max": round(max(lat), 2) if lat else 0.0,
        },
        "elapsed_sec": round(cost, 3),
    }


async def run_benchmark(sk_payload: dict, trace_payload: dict) -> dict:
    # Warmup to reduce first-call skew.
    _ = await run_case_sk(sk_payload, CaseConfig(name="warmup_sk", total=4, concurrency=2))
    _ = await run_case_trace(trace_payload, CaseConfig(name="warmup_trace", total=2, concurrency=1))

    cases_sk = [
        CaseConfig(name="sk_c2", total=40, concurrency=2),
        CaseConfig(name="sk_c8", total=80, concurrency=8),
    ]
    cases_trace = [
        CaseConfig(name="trace_c2", total=12, concurrency=2),
        CaseConfig(name="trace_c4", total=12, concurrency=4),
    ]

    result = {
        "meta": {
            "gil_enabled": getattr(__import__("sys"), "_is_gil_enabled", lambda: None)(),
        },
        "sk": [await run_case_sk(sk_payload, cfg) for cfg in cases_sk],
        "trace": [await run_case_trace(trace_payload, cfg) for cfg in cases_trace],
    }
    return result


if __name__ == "__main__":
    args = parse_args()
    sk_payload = json.loads(Path(args.sk_payload).read_text(encoding="utf-8"))
    trace_payload = json.loads(Path(args.trace_payload).read_text(encoding="utf-8"))
    result = asyncio.run(run_benchmark(sk_payload, trace_payload))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
