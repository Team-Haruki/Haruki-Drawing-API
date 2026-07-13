"""The honor badge as ONE plot.py widget tree, drawn by whichever backend renders it.

``HonorBadgeBox`` is a transcription of the old pure-Pillow ``_compose_full_honor_image_sync``
into ``Painter`` primitives. The badge is an absolute-coordinate composite (fixed canvas size
per honor type, background, frame overlay, chara icons at computed offsets, measured text, rank
stars, alpha mask), not a flow layout, so it takes the same shape as
``profile.drawer.CardFullThumbnailBox``: a custom ``Widget`` whose ``_draw_content`` emits raw
absolute-coordinate ops. Pillow executes them; ``IRPainter`` translates the same ops into Render
IR. One layout, both backends — the badge geometry no longer exists twice.

Op-for-op notes (the Pillow output is the ground truth this reproduces pixel for pixel):

- the base art (``honor_img`` / the bonds background) IS the canvas in the legacy composer, so it
  goes through ``paste_src`` (Porter-Duff Src, a verbatim four-channel write). ``paste`` would
  square the alpha of its anti-aliased corners over the empty canvas, and ``paste_with_alpha_blend``
  would zero the rgb UNDER those transparent corners — which Pillow's paste-lerp reads back when
  the frame's AA edge crosses them (up to 228/255 on ~200 px). See ``Painter.paste_src``.
- every OVERLAY (frame, level icons, rank, scroll, word, stars, the bonds left half, the empty
  slot art) keeps the legacy ``img.paste(x, pos, x)`` alpha-lerp via ``Painter.paste``.
- the two bonds chara icons are resized 0.8x and cropped at the mid-line IN PYTHON, in the legacy
  order (resize-then-crop). Passing ``src_rect`` instead would crop in SOURCE pixels BEFORE the
  fit — the opposite order, and the reason the hand-built IR drifted from Pillow by up to 52/255
  on 2387 px. They go through ``paste_with_alpha_blend`` because they sit on the OPAQUE bonds
  background, where src-over and Pillow's lerp give the same rgb; the lerp would additionally drag
  the layer's alpha down at the icons' AA edges, and the mask is what defines the badge silhouette.
- ``push_mask``/``pop_mask`` (alpha multiply = Skia's DstIn) replaces
  ``img.putalpha(mask.split()[3])``. Because the masked layer is opaque (solid background + the
  src-over icons), multiply and putalpha's replace agree pixel for pixel — one mask semantic on
  both backends.

The ONE intentional deviation from the legacy Pillow output, in the bonds branch when the request
carries NO mask (unreachable for real bonds honors — the mask is what gives the badge its shape):
the legacy lerp-pasted the icons and had no ``putalpha`` to undo the alpha it dragged down, so the
badge kept translucent speckles (alpha 191-252) along the icons' AA outlines, over an opaque
background. Src-over leaves them opaque, which is what the Skia backend has always rendered. RGB is
unchanged; every other branch — including the empty slot, all three rank overlays, and bonds with a
mask — is byte-identical to the composer this replaced.
"""

from __future__ import annotations

import os

from PIL import Image

from src.sekai.base.painter import (
    WHITE,
    Painter,
    ascender_top_to_painter_y,
    get_font,
    get_font_desc,
    get_text_size,
    resize_keep_ratio,
)
from src.sekai.base.plot import Canvas, Widget
from src.sekai.base.utils import ImageSource, resolve_image_source_sync
from src.settings import DEFAULT_BOLD_FONT

from .model import HonorRequest

# The bonds background is two halves: the right art full-bleed, the left art's left half on top,
# overlapping the centre line by this much.
BONDS_BACKGROUND_CENTER_OVERLAP = 3

FCAP_TEXT_SIZE = 22
FCAP_TEXT_TOP_Y = 46  # ImageDraw's "la" anchor y in the legacy composer


def honor_group_uses_scroll_level(group_type: str | None) -> bool:
    return group_type in {"fc_ap", "event", "wl_event"}


def is_world_link_rank_style(group_type: str | None, rank_img_path: str | None) -> bool:
    if not rank_img_path:
        return False
    normalized = rank_img_path.replace("\\", "/").lower()
    folder = os.path.basename(os.path.dirname(normalized))
    return folder.startswith("honor_top_") and "event" in folder


def resolve_event_rank_position(base: tuple[int, int], rank: tuple[int, int], is_main: bool) -> tuple[int, int]:
    # Some special event honors provide a full-width rank overlay instead of the usual compact
    # "TOP xxx" badge. Those assets should cover the whole honor.
    if rank[0] >= base[0] - 8 and rank[1] >= base[1] - 8:
        return (0, 0)
    return (190, 0) if is_main else (34, 42)


def _size_of(source: ImageSource) -> tuple[int, int]:
    return (int(source.size[0]), int(source.size[1]))


def honor_badge_size(rqd: HonorRequest, images: dict[str, ImageSource | None]) -> tuple[int, int] | None:
    """Canvas size of the badge, or ``None`` when the request is not renderable (the legacy
    composer returned ``None`` for exactly these cases and the caller falls back / errors)."""
    if rqd.is_empty:
        empty = images.get("empty_honor")
        if empty is None:
            return None
        w, h = _size_of(empty)
        return (w + 6, h + 6)
    if rqd.honor_type in ("normal", "birthday"):
        base = images.get("honor_img")
        return None if base is None else _size_of(base)
    if rqd.honor_type == "bonds":
        left, right = images.get("bonds_bg"), images.get("bonds_bg2")
        if left is None or right is None:
            return None
        return _size_of(right)
    return None


class HonorBadgeBox(Widget):
    """One honor badge (normal / birthday / bonds / empty slot) at its natural size."""

    def __init__(self, rqd: HonorRequest, images: dict[str, ImageSource | None]) -> None:
        super().__init__()
        size = honor_badge_size(rqd, images)
        assert size is not None, "HonorBadgeBox built from an unrenderable request"
        self.rqd = rqd
        self.images = images
        self.badge_size = size
        # NOTE no prefetch_image_sources here, on purpose: it would be dead weight. Canvas.get_img()
        # is what runs the ref prefetch, and neither honor path calls it — the Pillow side uses
        # get_img_sync() and the Skia side goes through build_canvas_ir(). Every honor image is a
        # PIL image already (load_honor_images decodes them), so there is nothing to prefetch.

    def _get_content_size(self) -> tuple[int, int]:
        return self.badge_size

    # ---- shared pieces ----

    def _add_frame(self, p: Painter, level: int | None = None) -> None:
        frame = self.images.get("frame_img")
        if frame is None:
            return
        p.paste(frame, (8, 0) if self.rqd.honor_rarity == "low" else (0, 0))
        if self.rqd.honor_type != "birthday":
            return
        icon = self.images.get("frame_degree_level_img")
        if icon is None or not level:
            return
        w, h = self.badge_size
        sz = 18
        for i in range(level):
            p.paste(icon, (int(w / 2 - sz * level / 2 + i * sz), h - sz), (sz, sz))

    def _add_lv_star(self, p: Painter, level: int) -> None:
        if level > 10:
            level = level - 10
        lv_img = self.images.get("lv_img")
        lv6_img = self.images.get("lv6_img")
        if lv_img is not None:
            for i in range(0, min(level, 5)):
                p.paste(lv_img, (50 + 16 * i, 61))
        if lv6_img is not None:
            for i in range(5, level):
                p.paste(lv6_img, (50 + 16 * (i - 5), 61))

    def _add_fcap_lv(self, p: Painter) -> None:
        text = str(self.rqd.fc_or_ap_level or "")
        font = get_font(path=DEFAULT_BOLD_FONT, size=FCAP_TEXT_SIZE)
        text_w, _ = get_text_size(font, text)
        offset = 215 if self.rqd.is_main_honor else 37
        y = ascender_top_to_painter_y(DEFAULT_BOLD_FONT, FCAP_TEXT_SIZE, FCAP_TEXT_TOP_Y)
        p.text(text, (offset + 50 - text_w // 2, y), get_font_desc(DEFAULT_BOLD_FONT, FCAP_TEXT_SIZE), fill=WHITE)

    # ---- branches ----

    def _draw_empty(self, p: Painter) -> None:
        p.paste(self.images["empty_honor"], (3, 3))

    def _draw_normal(self, p: Painter) -> None:
        rqd = self.rqd
        gtype = rqd.group_type
        base = self.images["honor_img"]
        p.paste_src(base, (0, 0))
        self._add_frame(p, rqd.honor_level)

        rank_img = self.images.get("rank_img")
        if rank_img:
            if gtype == "rank_match":
                rank_pos = (190, 0) if rqd.is_main_honor else (17, 42)
            elif is_world_link_rank_style(gtype, rqd.rank_img_path):
                rank_pos = (0, 0)
            else:
                rank_pos = resolve_event_rank_position(_size_of(base), _size_of(rank_img), rqd.is_main_honor)
            p.paste(rank_img, rank_pos)

        if honor_group_uses_scroll_level(gtype):
            scroll_img = self.images.get("scroll_img")
            if scroll_img is not None:
                p.paste(scroll_img, (215, 3) if rqd.is_main_honor else (37, 3))
            if gtype == "fc_ap" or scroll_img is not None:
                self._add_fcap_lv(p)
        elif gtype in ("character", "achievement"):
            self._add_lv_star(p, rqd.honor_level)

    def _draw_bonds_background(self, p: Painter) -> None:
        left = resolve_image_source_sync(self.images["bonds_bg"])
        right = self.images["bonds_bg2"]
        w, h = self.badge_size
        p.paste_src(right, (0, 0))
        if left.size != (w, h):
            left = left.resize((w, h), Image.Resampling.BILINEAR)
        left_width = min(w, w // 2 + BONDS_BACKGROUND_CENTER_OVERLAP)
        p.paste(left.crop((0, 0, left_width, h)), (0, 0))

    def _draw_bonds(self, p: Painter) -> None:
        rqd = self.rqd
        w, h = self.badge_size
        c1_src = self.images.get("chara_icon_1")
        c2_src = self.images.get("chara_icon_2")
        mask_img = self.images.get("mask_img")

        if c1_src is None or c2_src is None:
            # The legacy composer returns the bare background (no mask/frame/word/stars).
            self._draw_bonds_background(p)
            return

        c1_img = resolve_image_source_sync(c1_src)
        c2_img = resolve_image_source_sync(c2_src)
        # Legacy releases serialized chara_id as a string, so face_pos never matched.
        # Keep the observed center-anchor layout instead of re-enabling that table.
        c1_face = c1_img.size[0] // 2
        c2_face = c2_img.size[0] // 2

        scale = 0.8
        c1_img = resize_keep_ratio(c1_img, scale, mode="scale")
        c2_img = resize_keep_ratio(c2_img, scale, mode="scale")
        c1w, c1h = c1_img.size
        c2w, c2h = c2_img.size
        c1_face = int(c1_face * scale)
        c2_face = int(c2_face * scale)

        offset_to_mid = 120 if rqd.is_main_honor else 30
        mid = w // 2
        c1_face_x = mid - offset_to_mid
        c2_face_x = mid + offset_to_mid

        overlap1 = (c1_face_x - c1_face + c1w) - mid
        if overlap1 > 0:
            c1_img = c1_img.crop((0, 0, c1w - overlap1, c1h))
        overlap2 = mid - (c2_face_x - c2_face)
        if overlap2 > 0:
            c2_img = c2_img.crop((overlap2, 0, c2w, c2h))
            c2_face -= overlap2

        if mask_img is not None:
            p.push_mask(mask_img, (0, 0), (w, h))
        self._draw_bonds_background(p)
        p.paste_with_alpha_blend(c1_img, (c1_face_x - c1_face, h - c1h))
        p.paste_with_alpha_blend(c2_img, (c2_face_x - c2_face, h - c2h))
        if mask_img is not None:
            p.pop_mask()

        self._add_frame(p)
        if rqd.is_main_honor:
            word_img = self.images.get("word_img")
            if word_img is not None:
                ww, wh = _size_of(word_img)
                p.paste(word_img, (int(190 - ww / 2), int(40 - wh / 2)))
        self._add_lv_star(p, rqd.honor_level)

    def _draw_content(self, p: Painter) -> None:
        if self.rqd.is_empty:
            self._draw_empty(p)
        elif self.rqd.honor_type in ("normal", "birthday"):
            self._draw_normal(p)
        elif self.rqd.honor_type == "bonds":
            self._draw_bonds(p)


def build_honor_badge_canvas(rqd: HonorRequest, images: dict[str, ImageSource | None]) -> Canvas | None:
    """The badge as a standalone ``Canvas``, or ``None`` when the request is not renderable.

    Both backends consume this: ``drawer.compose_full_honor_image`` renders it with Pillow, and
    ``honor.skia.try_render_full_honor_payload`` splices its IR under the watermark footer.
    """
    if honor_badge_size(rqd, images) is None:
        return None
    with Canvas() as canvas:
        HonorBadgeBox(rqd, images)
    return canvas
