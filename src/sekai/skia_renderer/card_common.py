"""Shared Render IR v2 layout helpers for the card endpoints (port steps ④/⑤).

These build IR v2 nodes via :class:`IRBuilder` and are reused by both the Card
List and Card Box scene builders. Values mirror the Rust ``card_scene.rs``.
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from src.sekai.skia_renderer.ir_builder import IRBuilder

# The payload cache moved to its own module so the generic render path and the global cache
# reporting can reach it without importing these card layout helpers. Re-exported here because
# card/misc/honor and card_render import it from this path.
from src.sekai.skia_renderer.payload_cache import (  # noqa: F401
    _skia_payload_cache,
    _SkiaPayloadCache,
    get_skia_payload_cached,
    put_skia_payload_cache,
)

# Layout constants shared by the card endpoints (mirror Rust lib.rs / card_scene.rs).
BG_PADDING = 20.0
GRID_PADDING = 16.0
THUMB = 100.0
TITLE_H = 50.0
TITLE_SEP = 16.0
WATERMARK_FALLBACK = "Haruki Drawing API"

Color4 = tuple[int, int, int, int]


def validate_asset_path(path: str | None, *, field: str) -> str | None:
    """Reject absolute / backslash / ``..`` asset paths (shared by both card renderers)."""
    if path is None or path == "":
        return None
    if "\\" in path:
        raise ValueError(f"{field} must use forward slash asset paths")
    posix = PurePosixPath(path)
    windows = PureWindowsPath(path)
    if posix.is_absolute() or windows.is_absolute():
        raise ValueError(f"{field} must be relative to assets_base_dir")
    if ".." in posix.parts:
        raise ValueError(f"{field} must not contain '..'")
    return path


def thumbnail_to_ir(thumbnail: Any) -> dict[str, Any]:
    """Serialize a card thumbnail request to the v1 IR dict (shared by list + box)."""
    rare_img_path = thumbnail.birthday_icon_path if thumbnail.rare == "rarity_birthday" else thumbnail.rare_img_path
    return {
        "card_id": thumbnail.card_id,
        "card_thumbnail_path": validate_asset_path(thumbnail.card_thumbnail_path, field="card_thumbnail_path"),
        "rare": thumbnail.rare,
        "frame_img_path": validate_asset_path(thumbnail.frame_img_path, field="frame_img_path"),
        "attr_img_path": validate_asset_path(thumbnail.attr_img_path, field="attr_img_path"),
        "rare_img_path": validate_asset_path(rare_img_path, field="rare_img_path"),
        "train_rank": thumbnail.train_rank,
        "train_rank_img_path": validate_asset_path(thumbnail.train_rank_img_path, field="train_rank_img_path"),
        "level": thumbnail.level,
        "custom_text": thumbnail.custom_text,
        "is_after_training": thumbnail.is_after_training,
        "is_pcard": thumbnail.is_pcard,
    }


def rare_count(rare: str) -> int:
    if rare == "rarity_birthday":
        return 1
    for ch in rare:
        if ch.isascii() and ch.isdigit():
            return int(ch)
    return 0


def parse_color(code: str | None) -> tuple[int, int, int]:
    """Parse ``#rrggbb`` to an (r, g, b) tuple; mirror of Rust ``parse_color``."""
    if code:
        hexs = code.strip().lstrip("#")
        if len(hexs) == 6:
            try:
                return int(hexs[0:2], 16), int(hexs[2:4], 16), int(hexs[4:6], 16)
            except ValueError:
                pass
    return 204, 204, 204


def limited_icon_path(supply_type: str, icons: dict[str, Any]) -> str | None:
    if supply_type in ("期间限定", "WL限定", "联动限定"):
        return icons.get("term_limited")
    if supply_type in ("Fes限定", "CFes限定", "BFes限定"):
        return icons.get("fes_limited")
    return None


def center_text(
    b: IRBuilder, text: str, role: str, size: float, rx: float, ry: float, rw: float, rh: float, fill: Color4
) -> None:
    # TextBox centers Pillow's ink bbox inside the content width and places the logical
    # text top inside its 2px vertical padding. Resolve both with Pillow metrics so Skia
    # receives the same left origin and alphabetic baseline.
    text_w, _ = b.measure_text_ink(text, role, size)
    text_x = rx + (rw - text_w) // 2
    text_top = ry + (rh - size) // 2
    baseline_y = b.painter_baseline_y(text_top, role, size)
    b.text(text, (text_x, baseline_y), role, size, baseline="alphabetic", fill=fill)


def notice_title(b: IRBuilder, x: float, y: float, width: float, title: str) -> None:
    b.blurglass((x, y), (width, TITLE_H), 10, (255, 246, 219, 220), shadow_alpha=0.24)
    label = "提示"
    label_w, _ = b.measure_text_ink(label, "bold", 22)
    text_top = y + 16
    label_baseline = b.painter_baseline_y(text_top, "bold", 22)
    title_baseline = b.painter_baseline_y(text_top, "default", 22)
    b.text(label, (x + 16, label_baseline), "bold", 22, baseline="alphabetic", fill=(166, 90, 0, 255))
    b.text(
        title,
        (x + 32 + label_w, title_baseline),
        "default",
        22,
        baseline="alphabetic",
        fill=(98, 68, 0, 255),
    )


def thumbnail(b: IRBuilder, thumb: dict[str, Any], size: float) -> None:
    """Layered thumbnail composite in a ``size``x``size`` local frame.

    The legacy path composes at 100x100 then scales; we scale each layer offset
    by ``s`` at build time so the result stays in absolute coords.
    """
    s = size / 100.0
    if thumb.get("card_thumbnail_path"):
        b.image(thumb["card_thumbnail_path"], (0, 0), (size, size), fit="cover")
    if thumb.get("is_pcard"):
        b.rect((0, 76 * s), (100 * s, 24 * s), fill=(70, 70, 100, 255))
        text = thumb.get("custom_text") or f"Lv.{thumb.get('level') or 0}"
        b.text(text, (6 * s, 92 * s), "bold", 20 * s, baseline="alphabetic", fill=(255, 255, 255, 255))
    if thumb.get("frame_img_path"):
        b.image(thumb["frame_img_path"], (0, 0), (size, size), fit="stretch")
    if thumb.get("attr_img_path"):
        b.image(thumb["attr_img_path"], (1 * s, 0), (22 * s, 25 * s), fit="stretch")
    if thumb.get("is_pcard") and (thumb.get("train_rank") or 0) > 0 and thumb.get("train_rank_img_path"):
        b.image(thumb["train_rank_img_path"], (65 * s, 65 * s), (35 * s, 35 * s), fit="stretch")
    rare_path = thumb.get("rare_img_path")
    if rare_path:
        rare_w = rare_h = 17 * s
        voffset = 24 * s if thumb.get("is_pcard") else 6 * s
        for i in range(rare_count(thumb["rare"])):
            b.image(rare_path, (6 * s + rare_w * i, size - rare_h - voffset), (rare_w, rare_h), fit="stretch")
