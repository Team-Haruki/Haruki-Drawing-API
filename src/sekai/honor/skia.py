"""Skia Render-IR path for the /honor endpoint (badge scene + chart-style watermark shell).

``drawer._compose_full_honor_image_sync`` (pure Pillow) stays the ground truth; this module
rebuilds the same absolute-coordinate composite as IR v2 nodes:

- ``Image`` nodes with ``source_rect`` crops cover the bonds half-background paste and the
  chara-icon mid-line crops;
- ``group(mask=...)`` (saveLayer + DstIn) covers ``img.putalpha(mask.split()[3])``;
- the raster watermark footer the route otherwise adds via ``add_request_watermark_to_image``
  is replicated chart-style in two native passes: pass 1 renders the badge scene to PNG
  bytes, pass 2 draws that as an encoded mem image plus the stretched bottom-strip footer
  sample (``source_rect``) and the right-aligned white/grey shadowed watermark lines.
  Python never touches pixels.

Layout decisions (canvas size, crop windows, text measurement) run in Python against the
asset headers / PIL font metrics so every coordinate matches the Pillow composer. Any
unsupported shape or unreadable *required* asset returns ``None`` so the caller falls back
to the Pillow path, which raises the canonical user-visible error.
"""

from __future__ import annotations

import json
import logging
import time

from PIL import Image

from src.core.debug import set_render_backend
from src.core.heavy_render_pool import EncodedImagePayload
from src.sekai.base.draw import (
    WATERMARK_BOTTOM_OFFSET,
    WATERMARK_LINE_SEP,
    WATERMARK_RIGHT_OFFSET,
    WATERMARK_SHADOW_OFFSET,
    WATERMARK_TOP_OFFSET,
    build_request_watermark_text,
    get_watermark_render_spec,
)
from src.sekai.base.painter import get_font, get_text_size
from src.sekai.base.utils import run_in_pool
from src.sekai.skia_renderer.canvas import load_native_renderer, payload_from_native, skia_plot_enabled
from src.sekai.skia_renderer.ir_builder import IRBuilder
from src.sekai.skia_renderer.payload_cache import get_skia_payload_cached, put_skia_payload_cache
from src.sekai.skia_renderer.render_stats import (
    OUTCOME_CACHE_HIT,
    OUTCOME_DISABLED,
    OUTCOME_ERROR,
    OUTCOME_FALLBACK,
    OUTCOME_SKIA,
    backend_for_outcome,
    record_native_metrics,
    record_render,
)
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT, EXPORT_IMAGE_FORMAT, FONT_DIR, JPG_QUALITY

from .model import HonorRequest

logger = logging.getLogger("honor.draw.perf")

# /render-stats + the ``backend=`` log field key on this name (the route is /api/pjsk/honor, and
# every parity payload variant — honor/honor_bonds/honor_birthday/honor_fcap — is this one
# endpoint). Like the chart path, this module hand-builds its IR around a two-pass watermark
# shell, so it never goes through render_canvas_payload and has to record its own outcome —
# exactly one per render attempt, mirroring the canvas helper.
HONOR_ENDPOINT = "honor"


def _record(outcome: str, payload: EncodedImagePayload | None = None) -> None:
    """Record one render attempt for /render-stats and tag the request context.

    Mirrors ``src.sekai.skia_renderer.canvas._record`` (and ``chart.drawer._record``): this path
    cannot reuse the canvas helper, so it records through the same public primitives instead.
    Folding in ``native_metrics`` is what keeps a broken font config visible — otherwise
    /render-stats would report 0 font fallbacks while every honor badge rendered in sans-serif.
    """
    record_render(HONOR_ENDPOINT, outcome)
    backend = backend_for_outcome(outcome)
    set_render_backend(backend)
    if payload is not None:
        payload.backend = backend
        record_native_metrics(payload.native_metrics)


def _image_size(path: str | None) -> tuple[int, int] | None:
    """Natural pixel size straight from the file header (no pixel decode).

    Joins like ``get_img_from_path`` (leading-slash safe). ``None`` = missing/unreadable.
    """
    if not path or not path.strip():
        return None
    try:
        with Image.open(ASSETS_BASE_DIR / path.lstrip("/")) as img:
            return img.size
    except (FileNotFoundError, OSError, ValueError):
        return None


def _new_builder(width: int, height: int, export_format: str = "png") -> IRBuilder:
    return IRBuilder(
        width,
        height,
        assets_base_dir=str(ASSETS_BASE_DIR),
        font_dir=str(FONT_DIR),
        default_font=DEFAULT_FONT,
        bold_font=DEFAULT_BOLD_FONT,
        export_format=export_format,
        jpg_quality=JPG_QUALITY,
    )


def _resolve_event_rank_pos(base: tuple[int, int], rank: tuple[int, int], is_main: bool) -> tuple[int, int]:
    """Size-based mirror of ``drawer.resolve_event_rank_position`` (full-cover overlays at 0,0)."""
    if rank[0] >= base[0] - 8 and rank[1] >= base[1] - 8:
        return (0, 0)
    return (190, 0) if is_main else (34, 42)


def _build_badge_scene(rqd: HonorRequest) -> IRBuilder | None:
    """IR mirror of ``drawer._compose_full_honor_image_sync`` (same branches and coordinates).

    Returns ``None`` when the Pillow path must handle the request instead: either the
    composer would return ``None`` too, or a *required* asset cannot be probed (Pillow then
    raises the canonical error). Only ``rank_img`` is optional, matching
    ``load_optional_image``.
    """
    from src.sekai.honor.drawer import (  # lazy: drawer re-exports from this module at its end
        BONDS_BACKGROUND_CENTER_OVERLAP,
        honor_group_uses_scroll_level,
        is_world_link_rank_style,
    )

    is_main = rqd.is_main_honor
    htype = rqd.honor_type
    hlv = rqd.honor_level

    # Assets the Pillow composer loads unconditionally (raising when missing).
    lv_size = _image_size(rqd.lv_img_path) if rqd.lv_img_path else None
    if rqd.lv_img_path and lv_size is None:
        return None
    lv6_size = _image_size(rqd.lv6_img_path) if rqd.lv6_img_path else None
    if rqd.lv6_img_path and lv6_size is None:
        return None
    frame_size = _image_size(rqd.frame_img_path) if rqd.frame_img_path else None
    if rqd.frame_img_path and frame_size is None:
        return None
    icon_available = False
    if htype == "birthday" and rqd.frame_degree_level_img_path:
        if _image_size(rqd.frame_degree_level_img_path) is None:
            return None
        icon_available = True

    def add_frame(b: IRBuilder, w: int, h: int, rarity: str | None, level: int | None = None) -> None:
        if frame_size is None:
            return
        b.image(rqd.frame_img_path, (8, 0) if rarity == "low" else (0, 0), frame_size)
        if htype == "birthday":
            if not icon_available or not level:
                return
            sz = 18
            for i in range(level):
                b.image(rqd.frame_degree_level_img_path, (int(w / 2 - sz * level / 2 + i * sz), h - sz), (sz, sz))

    def add_lv_star(b: IRBuilder, level: int) -> None:
        if level > 10:
            level = level - 10
        if lv_size is not None:
            for i in range(0, min(level, 5)):
                b.image(rqd.lv_img_path, (50 + 16 * i, 61), lv_size)
        if lv6_size is not None:
            for i in range(5, level):
                b.image(rqd.lv6_img_path, (50 + 16 * (i - 5), 61), lv6_size)

    def add_fcap_lv(b: IRBuilder) -> None:
        lv_text = str(rqd.fc_or_ap_level or "")
        font = get_font(path=DEFAULT_BOLD_FONT, size=22)
        text_w, _ = get_text_size(font, lv_text)
        offset = 215 if is_main else 37
        # PIL ImageDraw.text default anchor is left/top-of-ascent -> IR "ascender" baseline.
        b.text(lv_text, (offset + 50 - text_w // 2, 46), "bold", 22, baseline="ascender", fill=(255, 255, 255, 255))

    if rqd.is_empty:
        empty_size = _image_size(rqd.empty_honor_path) if rqd.empty_honor_path else None
        if rqd.empty_honor_path and empty_size is None:
            return None
        if empty_size is None:
            return None
        padding = 3
        b = _new_builder(empty_size[0] + padding * 2, empty_size[1] + padding * 2)
        b.image(rqd.empty_honor_path, (padding, padding), empty_size)
        return b

    if htype in ("normal", "birthday"):
        rarity = rqd.honor_rarity
        gtype = rqd.group_type
        base_size = _image_size(rqd.honor_img_path) if rqd.honor_img_path else None
        if rqd.honor_img_path and base_size is None:
            return None
        if base_size is None:
            return None
        w, h = base_size
        b = _new_builder(w, h)
        b.image(rqd.honor_img_path, (0, 0), (w, h))
        add_frame(b, w, h, rarity, hlv)

        rank_size = _image_size(rqd.rank_img_path) if rqd.rank_img_path else None  # optional asset
        if rank_size is not None:
            if gtype == "rank_match":
                rank_pos = (190, 0) if is_main else (17, 42)
            elif is_world_link_rank_style(gtype, rqd.rank_img_path):
                rank_pos = (0, 0)
            else:
                rank_pos = _resolve_event_rank_pos(base_size, rank_size, is_main)
            b.image(rqd.rank_img_path, rank_pos, rank_size)

        if honor_group_uses_scroll_level(gtype):
            scroll_size = _image_size(rqd.scroll_img_path) if rqd.scroll_img_path else None
            if rqd.scroll_img_path and scroll_size is None:
                return None
            if scroll_size is not None:
                b.image(rqd.scroll_img_path, (215, 3) if is_main else (37, 3), scroll_size)
            if gtype == "fc_ap" or scroll_size is not None:
                add_fcap_lv(b)
        elif gtype in ("character", "achievement"):
            add_lv_star(b, hlv)
        return b

    if htype == "bonds":
        rarity = rqd.honor_rarity
        left_size = _image_size(rqd.bonds_bg_path) if rqd.bonds_bg_path else None
        if rqd.bonds_bg_path and left_size is None:
            return None
        right_size = _image_size(rqd.bonds_bg_path2) if rqd.bonds_bg_path2 else None
        if rqd.bonds_bg_path2 and right_size is None:
            return None
        if left_size is None or right_size is None:
            return None
        c1_size = _image_size(rqd.chara_icon_path) if rqd.chara_icon_path else None
        if rqd.chara_icon_path and c1_size is None:
            return None
        c2_size = _image_size(rqd.chara_icon_path2) if rqd.chara_icon_path2 else None
        if rqd.chara_icon_path2 and c2_size is None:
            return None
        mask_path = rqd.mask_img_path
        if mask_path and _image_size(mask_path) is None:
            return None
        word_size = _image_size(rqd.word_img_path) if rqd.word_img_path else None
        if rqd.word_img_path and word_size is None:
            return None

        w, h = right_size
        b = _new_builder(w, h)

        def bonds_background(bb: IRBuilder) -> None:
            # _paste_bonds_background: right bg full, left bg's left half (mid + overlap) on top.
            bb.image(rqd.bonds_bg_path2, (0, 0), (w, h))
            left_width = min(w, w // 2 + BONDS_BACKGROUND_CENTER_OVERLAP)
            lw, lh = left_size
            if (lw, lh) == (w, h):
                source = (0.0, 0.0, float(left_width), float(lh))
            else:  # Pillow resizes left to the right bg's size before cropping
                source = (0.0, 0.0, left_width * lw / w, float(lh))
            bb.image(rqd.bonds_bg_path, (0, 0), (left_width, h), source_rect=source)

        if c1_size is None or c2_size is None:
            # Pillow returns the bare background composite (no mask/frame/word/stars).
            bonds_background(b)
            return b

        # Center-anchored face layout (see the drawer's legacy chara_id note): 0.8x scale,
        # then crop each icon at the canvas mid-line.
        c1w0, c1h0 = c1_size
        c2w0, c2h0 = c2_size
        scale = 0.8
        c1w, c1h = int(c1w0 * scale), int(c1h0 * scale)
        c2w, c2h = int(c2w0 * scale), int(c2h0 * scale)
        c1_face = int((c1w0 // 2) * scale)
        c2_face = int((c2w0 // 2) * scale)

        offset_to_mid = 120 if is_main else 30
        mid = w // 2
        c1_face_x = mid - offset_to_mid
        c2_face_x = mid + offset_to_mid

        overlap1 = (c1_face_x - c1_face + c1w) - mid
        c1_draw_w = c1w - overlap1 if overlap1 > 0 else c1w
        overlap2 = mid - (c2_face_x - c2_face)
        c2_crop = overlap2 if overlap2 > 0 else 0
        c2_draw_w = c2w - c2_crop
        c2_face -= c2_crop

        def bonds_children(bb: IRBuilder) -> None:
            bonds_background(bb)
            if c1_draw_w <= 0 or c2_draw_w <= 0:
                return
            c1_src = None if c1_draw_w == c1w else (0.0, 0.0, c1_draw_w * c1w0 / c1w, float(c1h0))
            bb.image(rqd.chara_icon_path, (c1_face_x - c1_face, h - c1h), (c1_draw_w, c1h), source_rect=c1_src)
            c2_src = None if c2_crop == 0 else (c2_crop * c2w0 / c2w, 0.0, float(c2w0), float(c2h0))
            bb.image(rqd.chara_icon_path2, (c2_face_x - c2_face, h - c2h), (c2_draw_w, c2h), source_rect=c2_src)

        if mask_path:
            # putalpha(mask.split()[3]) over the bg+icon composite only (frame/word/stars after).
            with b.group((0, 0), (w, h), mask=mask_path):
                bonds_children(b)
        else:
            bonds_children(b)

        add_frame(b, w, h, rarity)
        if is_main and word_size is not None:
            b.image(rqd.word_img_path, (int(190 - word_size[0] / 2), int(40 - word_size[1] / 2)), word_size)
        add_lv_star(b, hlv)
        return b

    return None


async def try_render_full_honor_payload(rqd: HonorRequest) -> EncodedImagePayload | None:
    """Skia path for the /honor route: badge scene + the route's raster watermark footer
    (``add_request_watermark_to_image`` equivalent) rendered natively in two passes.
    Returns ``None`` (gate off / unsupported / failure) so the caller falls back to Pillow.
    """
    if not skia_plot_enabled():
        _record(OUTCOME_DISABLED)
        return None
    try:
        native = load_native_renderer()
    except ImportError as exc:
        logger.error("haruki_skia_renderer not importable (%s); falling back to Pillow", exc)
        _record(OUTCOME_FALLBACK)
        return None

    from src.sekai.honor.drawer import _build_full_honor_cache_key  # lazy: avoids a circular import

    # The cached payload embeds the footer, so the key must cover everything the footer text
    # derives from (dt/timezone) on top of the Pillow composed key (which excludes timezone).
    watermark_text = build_request_watermark_text(rqd)
    cache_key = f"{_build_full_honor_cache_key(rqd)}|skia|{EXPORT_IMAGE_FORMAT}|{JPG_QUALITY}|wm:{watermark_text}"
    cached = get_skia_payload_cached(cache_key)
    if cached is not None:
        _record(OUTCOME_CACHE_HIT, cached)
        return cached

    def _render():
        badge = _build_badge_scene(rqd)
        if badge is None:
            return None
        w, h = badge.width, badge.height

        # Single pass: badge nodes + stretched bottom-strip footer (a SelfImage snapshot of
        # the badge rows just rendered above it) + shadowed watermark lines (mirrors
        # add_watermark_to_image; same spec as the chart watermark shell).
        font_size, lines, text_w, text_h = get_watermark_render_spec(watermark_text, w - WATERMARK_RIGHT_OFFSET, 12)
        footer_h = WATERMARK_TOP_OFFSET + text_h + WATERMARK_BOTTOM_OFFSET + WATERMARK_SHADOW_OFFSET
        b = _new_builder(w, h + footer_h, export_format=EXPORT_IMAGE_FORMAT)
        # Clip to the badge rect: the old pass-1 canvas cropped any overflow at (w, h),
        # matching the Pillow composer's canvas bounds.
        with b.group((0, 0), (w, h), clip={"kind": "rect"}):
            b.splice_root_children(badge)
        sample_h = max(1, min(h, footer_h))
        b.self_image((0, h), (w, footer_h), source_rect=(0, h - sample_h, w, h))
        font = get_font(DEFAULT_FONT, font_size)
        x = max(0, w - text_w - WATERMARK_RIGHT_OFFSET)
        y = h + WATERMARK_TOP_OFFSET
        for idx, line in enumerate(lines):
            line_w = get_text_size(font, line)[0]
            lx = x + max(0, text_w - line_w)
            ly = y + idx * (font_size + WATERMARK_LINE_SEP)
            # PIL ImageDraw.text default anchor is left/top-of-ascent -> IR "ascender" baseline.
            b.text(line, (lx + 1, ly + 1), "default", font_size, baseline="ascender", fill=(75, 75, 75, 255))
            b.text(line, (lx, ly), "default", font_size, baseline="ascender", fill=(255, 255, 255, 255))
        ir_json = json.dumps(b.build(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return native.render_scene(ir_json, {})

    started = time.perf_counter()
    try:
        result = await run_in_pool(_render)
        if result is None:
            # _build_badge_scene declined (unsupported honor shape / unreadable required asset):
            # a fallback, not an error — Pillow renders it and raises the canonical message.
            _record(OUTCOME_FALLBACK)
            return None
        payload = payload_from_native(result)
    except Exception:
        logger.exception("honor backend=skia failed; falling back to Pillow")
        _record(OUTCOME_ERROR)
        return None
    _record(OUTCOME_SKIA, payload)
    logger.info(
        "honor backend=skia total=%.3fs bytes=%d image=%sx%s",
        time.perf_counter() - started,
        len(payload.image_bytes),
        payload.image_width,
        payload.image_height,
    )
    put_skia_payload_cache(cache_key, payload, len(payload.image_bytes))
    return payload
