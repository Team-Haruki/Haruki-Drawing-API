"""Compare platform Skia and native FreeType BASIC against Pillow's BASIC oracle.

Run with the matching rebuilt extension:
    uv run python -X gil=0 scripts/skia_text_parity.py

The report distinguishes advance/bounds drift from foreground-only pixel error.
An opaque white background avoids undefined RGB beneath transparent pixels.
This is a correctness audit, not a benchmark. RAQM/Pilmoji are different contracts.
"""

from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import haruki_skia_renderer as native
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from src.core.path_safety import resolve_cli_path
from src.sekai.skia_renderer.ir_builder import IRBuilder

TEXTS = ["未来 日本語テスト", "Haruki AVATAR To office", "j é e\u0301", "你好 abc 123", "  A  ", "", " "]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "out" / "native-text-parity")
    parser.add_argument("--font-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    args.out_dir = resolve_cli_path(args.out_dir)
    args.font_dir = resolve_cli_path(args.font_dir, must_exist=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    previews = []
    for weight in ("Regular", "Bold", "Heavy"):
        path = resolve_cli_path(args.font_dir / f"SourceHanSansSC-{weight}.otf", must_exist=True)
        for size in (8, 12, 16, 20, 24, 32, 48):
            font = ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.BASIC)
            for text in TEXTS:
                expected = Image.new("RGB", (800, 90), "white")
                ImageDraw.Draw(expected).text((15, 65), text, font=font, fill="black", anchor="ls")
                reference = np.asarray(expected).astype(np.int16)
                row = {
                    "font": weight,
                    "size": size,
                    "text": text,
                    "pillow_advance": font.getlength(text),
                    "pillow_bbox": font.getbbox(text),
                }
                panels = [expected]
                for engine in ("skia", "freetype_basic"):
                    metrics = native.measure_text_batch("", str(path), [(text, size)], engine=engine)[0]
                    b = IRBuilder(
                        800,
                        90,
                        assets_base_dir=str(args.font_dir),
                        font_dir=str(args.font_dir),
                        default_font=path.name,
                        bold_font=path.name,
                    )
                    b.rect((0, 0), (800, 90), fill=(255, 255, 255, 255))
                    b.text(text, (15, 65), "default", size, baseline="alphabetic", engine=engine)
                    payload = native.render_scene(json.dumps(b.build()).encode(), {})
                    actual = Image.open(BytesIO(payload["image_bytes"])).convert("RGB")
                    pixels = np.asarray(actual).astype(np.int16)
                    diff = np.abs(reference - pixels)
                    foreground = np.any((reference != 255) | (pixels != 255), axis=2)
                    row[engine] = {
                        "advance_error": abs(metrics["advance"] - font.getlength(text)),
                        "bbox_max_error": float(
                            np.max(np.abs(np.array(metrics["pillow_bbox"]) - np.array(font.getbbox(text))))
                        ),
                        "pixel_max": int(diff.max()),
                        "foreground_mean": float(diff[foreground].mean()) if foreground.any() else 0.0,
                    }
                    panels.append(actual)
                rows.append(row)
                if size == 32 and text == TEXTS[1]:
                    panel = Image.new("RGB", (800, 290), "white")
                    for i, (label, img) in enumerate(
                        zip(
                            (f"{weight}: Pillow BASIC", "Platform Skia", "Native FreeType BASIC + Skia"),
                            panels,
                            strict=True,
                        )
                    ):
                        panel.paste(img, (0, 20 + 90 * i))
                        ImageDraw.Draw(panel).text((15, 8 + 90 * i), label, fill="black")
                    previews.append(panel)
    summary = {
        engine: {
            "cases": len(rows),
            "metric_mismatches": sum(r[engine]["advance_error"] != 0 or r[engine]["bbox_max_error"] != 0 for r in rows),
            "pixel_max": max(r[engine]["pixel_max"] for r in rows),
            "mean_foreground_error": float(np.mean([r[engine]["foreground_mean"] for r in rows])),
        }
        for engine in ("skia", "freetype_basic")
    }
    report = {"ir_capability": native.IR_CAPABILITY, "summary": summary, "cases": rows}
    resolve_cli_path(args.out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    preview = Image.new("RGB", (800, 290 * len(previews)), "white")
    for i, panel in enumerate(previews):
        preview.paste(panel, (0, 290 * i))
    preview.save(resolve_cli_path(args.out_dir / "comparison.png"))
    print(json.dumps(summary, indent=2))  # noqa: T201
    return int(summary["freetype_basic"]["metric_mismatches"] > 0 or summary["freetype_basic"]["pixel_max"] > 1)


if __name__ == "__main__":
    raise SystemExit(main())
