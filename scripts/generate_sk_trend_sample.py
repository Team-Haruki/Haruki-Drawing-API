#!/usr/bin/env python3
"""
Generate sample SK player-trace data and render a trend image.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
import sys

from matplotlib import font_manager
from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


RANK_PATTERN = [5, 4, 6, 3, 7, 4, 6, 5, 3, 7]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate SK player-trace sample payload and chart image.")
    parser.add_argument("--output-dir", default="out/sk-trend-sample", help="Output directory for payload and image.")
    parser.add_argument("--event-id", type=int, default=123, help="Sample event id.")
    parser.add_argument("--region", default="jp", help="Region code.")
    parser.add_argument("--points", type=int, default=24, help="Number of rank points to generate.")
    parser.add_argument("--start-score", type=int, default=2_000_000, help="Initial score baseline.")
    return parser.parse_args()


def ensure_runtime_assets() -> None:
    from src.settings import ASSETS_BASE_DIR

    base_dir = Path(ASSETS_BASE_DIR)
    base_dir.mkdir(parents=True, exist_ok=True)

    tri_dir = base_dir / "lunabot_static_images" / "triangle"
    tri_dir.mkdir(parents=True, exist_ok=True)
    tri_colors = [
        (62, 136, 208, 255),
        (38, 116, 196, 255),
        (84, 156, 224, 255),
    ]
    for idx, color in enumerate(tri_colors, start=1):
        tri_path = tri_dir / f"tri{idx}.png"
        if tri_path.exists():
            continue
        img = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.polygon([(10, 118), (64, 10), (118, 118)], fill=color)
        img.save(tri_path)

    regular_font = Path(font_manager.findfont("DejaVu Sans"))
    bold_font = Path(font_manager.findfont(font_manager.FontProperties(family="DejaVu Sans", weight="bold")))
    font_targets = {
        "SourceHanSansSC-Regular.ttf": regular_font,
        "SourceHanSansSC-Bold.ttf": bold_font,
        "SourceHanSansSC-Heavy.ttf": bold_font,
    }
    for target_name, source_path in font_targets.items():
        target_path = base_dir / target_name
        if not target_path.exists():
            shutil.copy2(source_path, target_path)


def build_points(points: int, start_score: int):
    from src.sekai.sk.model import RankInfo

    now = datetime.now().replace(second=0, microsecond=0)
    start_time = now - timedelta(minutes=30 * (points - 1))
    score = start_score
    series: list[RankInfo] = []
    for i in range(points):
        score += 1_800 + (i % 5) * 220 + (i // 6) * 80
        series.append(
            RankInfo(
                rank=RANK_PATTERN[i % len(RANK_PATTERN)],
                name="DemoPlayer",
                score=score,
                time=start_time + timedelta(minutes=30 * i),
            )
        )
    return series


async def render_player_trace(output_dir: Path, event_id: int, region: str, points: int, start_score: int) -> dict:
    from src.sekai.sk.drawer import compose_player_trace_image
    from src.sekai.sk.model import PlayerTraceRequest

    ranks = build_points(points, start_score)
    request = PlayerTraceRequest(event_id=event_id, region=region, ranks=ranks)

    payload_path = output_dir / "sk_player_trace_payload.json"
    payload_path.write_text(json.dumps(request.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")

    image = await compose_player_trace_image(request)
    image_path = output_dir / "sk_player_trace.png"
    image.save(image_path)

    scores = [item.score for item in ranks if item.score is not None]
    rank_values = [item.rank for item in ranks]
    strict_increasing = all(scores[i] < scores[i + 1] for i in range(len(scores) - 1))
    rank_in_range = all(3 <= rank <= 7 for rank in rank_values)

    return {
        "payload_path": str(payload_path.resolve()),
        "image_path": str(image_path.resolve()),
        "points": len(ranks),
        "score_strictly_increasing": strict_increasing,
        "rank_min": min(rank_values),
        "rank_max": max(rank_values),
        "rank_between_3_and_7": rank_in_range,
    }


def build_sk_query_payload(output_dir: Path, event_id: int, region: str) -> Path:
    from src.sekai.sk.model import RankInfo, SKRequest

    now = datetime.now().replace(second=0, microsecond=0)
    aggregate_at = int((now + timedelta(hours=8)).timestamp() * 1000)
    request = SKRequest(
        id=event_id,
        region=region,
        name="SK Smoke Event",
        aggregate_at=aggregate_at,
        ranks=[
            RankInfo(rank=5, name="SmokePlayer", score=2_345_678, time=now),
        ],
        prev_ranks=RankInfo(rank=4, name="PrevPlayer", score=2_356_789, time=now - timedelta(minutes=30)),
        next_ranks=RankInfo(rank=6, name="NextPlayer", score=2_334_567, time=now - timedelta(minutes=30)),
    )
    payload_path = output_dir / "sk_query_payload.json"
    payload_path.write_text(json.dumps(request.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    return payload_path


def build_honor_payload(output_dir: Path) -> Path:
    from src.sekai.honor.model import HonorRequest

    request = HonorRequest(
        is_empty=True,
        empty_honor_path="lunabot_static_images/triangle/tri1.png",
    )
    payload_path = output_dir / "honor_payload.json"
    payload_path.write_text(json.dumps(request.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    return payload_path


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ensure_runtime_assets()
    summary = asyncio.run(
        render_player_trace(
            output_dir=output_dir,
            event_id=args.event_id,
            region=args.region,
            points=args.points,
            start_score=args.start_score,
        )
    )
    summary["sk_query_payload_path"] = str(build_sk_query_payload(output_dir, args.event_id, args.region).resolve())
    summary["honor_payload_path"] = str(build_honor_payload(output_dir).resolve())
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
