#!/usr/bin/env python3
"""Render a card endpoint via Pillow and Rust/Skia and emit a side-by-side comparison.

Renders the same request through the original Pillow composer and the new Skia
``render_*_payload`` path (which always uses Skia, ignoring the gray-launch gate),
then writes ``pillow.png``, ``skia.png``, a labelled ``side-by-side.png`` and a
``metrics.json`` (size match, byte sizes, MAE + alpha-IoU when sizes match).

Usage:
  uv run python scripts/compare_card_render.py --endpoint list \
    --payload out/rust-skia-card-list-test/card-list-compare-payload.json
  HARUKI_BG_TEST_HOUR=15.5 uv run python scripts/compare_card_render.py --endpoint box \
    --payload out/real-box/box-request.json --out out/compare-box

The payload is a CardListRequest (list) or CardBoxRequest (box) JSON. Set
HARUKI_BG_TEST_HOUR so both backends use the same time-based background palette.
The Pillow box composer can fail on synthetic payloads; the script still emits the
Skia render and records the error.
"""

from __future__ import annotations

import argparse
import asyncio
from io import BytesIO
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image, ImageDraw


def _decode(payload) -> Image.Image:
    return Image.open(BytesIO(payload.image_bytes)).convert("RGBA")


def _load_request(endpoint: str, path: Path):
    data = json.loads(path.read_text())
    if endpoint == "list":
        from src.sekai.card.model import CardListRequest

        return CardListRequest.model_validate(data)
    from src.sekai.card.model import CardBoxRequest

    return CardBoxRequest.model_validate(data)


async def _render_pillow(endpoint: str, rqd) -> Image.Image:
    if endpoint == "list":
        from src.sekai.card.drawer import compose_card_list_image

        return (await compose_card_list_image(rqd)).convert("RGBA")
    from src.sekai.card.drawer import compose_box_image

    return (await compose_box_image(rqd)).convert("RGBA")


async def _render_skia(endpoint: str, rqd) -> Image.Image:
    if endpoint == "list":
        from src.sekai.skia_renderer.card_list import render_card_list_payload

        return _decode(await render_card_list_payload(rqd))
    from src.sekai.skia_renderer.card_box import render_card_box_payload

    return _decode(await render_card_box_payload(rqd))


def _metrics(pillow: Image.Image | None, skia: Image.Image) -> dict:
    out: dict = {"skia_size": list(skia.size)}
    if pillow is None:
        out["pillow"] = "render failed (see error)"
        return out
    out["pillow_size"] = list(pillow.size)
    out["size_match"] = pillow.size == skia.size
    if pillow.size == skia.size:
        a = np.asarray(pillow).astype(int)
        b = np.asarray(skia).astype(int)
        am = a[:, :, 3] > 16
        bm = b[:, :, 3] > 16
        union = int(np.logical_or(am, bm).sum())
        out["mae"] = round(float(np.abs(a - b).mean()), 3)
        out["alpha_iou"] = round(int(np.logical_and(am, bm).sum()) / union, 4) if union else 1.0
    return out


def _side_by_side(pillow: Image.Image | None, skia: Image.Image, out_path: Path) -> None:
    band = 28
    panels = (
        [("Rust / Skia (new)", skia)]
        if pillow is None
        else [
            ("Pillow (original)", pillow),
            ("Rust / Skia (new)", skia),
        ]
    )
    gap = 14
    width = sum(p.width for _, p in panels) + gap * (len(panels) - 1)
    height = max(p.height for _, p in panels) + band
    canvas = Image.new("RGBA", (width, height), (32, 32, 32, 255))
    draw = ImageDraw.Draw(canvas)
    x = 0
    for label, panel in panels:
        draw.text((x + 6, 8), f"{label}  {panel.width}x{panel.height}", fill=(255, 255, 255, 255))
        canvas.paste(panel, (x, band))
        x += panel.width + gap
    canvas.save(out_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pillow vs Rust/Skia single-endpoint comparison.")
    parser.add_argument("--endpoint", choices=["list", "box"], required=True)
    parser.add_argument("--payload", type=Path, required=True, help="CardListRequest/CardBoxRequest JSON.")
    parser.add_argument("--out", type=Path, default=Path("out/compare"), help="Output directory.")
    args = parser.parse_args()

    rqd = _load_request(args.endpoint, args.payload)
    args.out.mkdir(parents=True, exist_ok=True)

    async def run() -> tuple[Image.Image | None, Image.Image, str | None]:
        skia = await _render_skia(args.endpoint, rqd)
        pillow: Image.Image | None = None
        err: str | None = None
        try:
            pillow = await _render_pillow(args.endpoint, rqd)
        except Exception as exc:  # Pillow box composer can reject some payloads.
            err = f"{type(exc).__name__}: {exc}"
        return pillow, skia, err

    pillow, skia, err = asyncio.run(run())

    if pillow is not None:
        pillow.save(args.out / "pillow.png")
    skia.save(args.out / "skia.png")
    _side_by_side(pillow, skia, args.out / "side-by-side.png")

    metrics = _metrics(pillow, skia)
    if err:
        metrics["pillow_error"] = err
    (args.out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2))

    sys.stdout.write(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.write(f"artifacts: {args.out}/side-by-side.png\n")
    if err:
        sys.stdout.write(f"pillow render failed: {err}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
