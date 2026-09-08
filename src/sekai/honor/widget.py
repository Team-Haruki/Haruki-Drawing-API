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
  slot art) keeps the legacy ``img.paste(x, pos, x)`` alpha-lerp via the explicit
  ``paste_resized_clipped(..., blend="paste_lerp")`` primitive.
- the two bonds chara icons use the shared ``BondsHonorPlan`` and
  ``Painter.paste_resized_clipped``: resize the FULL source 0.8x, then clip it at the destination
  mid-line. Passing ``src_rect`` would crop in SOURCE pixels BEFORE the fit — the opposite order,
  and the reason the old hand-built IR drifted from Pillow by up to 52/255 on 2387 px. The Pillow
  adapter performs that operation directly; IR lowers it to a rectangular Group clip around a
  full-source Image, so the native path does not create cropped ``mem:`` rasters.
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

from src.sekai.base.painter import (
    WHITE,
    Painter,
    get_font_desc,
)
from src.sekai.base.plot import Canvas, Widget
from src.sekai.base.utils import ImageSource
from src.settings import DEFAULT_BOLD_FONT

from .bonds_plan import FullResizeClipOp, build_bonds_honor_plan
from .model import HonorRequest

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
        # NOTE no prefetch_image_sources here, on purpose: the Pillow path resolves lazy refs
        # while replaying Painter operations in its worker, and IRPainter keeps the same refs
        # lazy through native decode. Eager prefetch would defeat the Skia path's no-Pillow rule.

    def _get_content_size(self) -> tuple[int, int]:
        return self.badge_size

    # ---- shared pieces ----

    def _paste_overlay(
        self,
        p: Painter,
        source: ImageSource,
        pos: tuple[int, int],
        size: tuple[int, int] | None = None,
    ) -> None:
        """Honor's legacy mask-paste, kept explicit instead of relying on generic IR paste."""

        resolved_size = _size_of(source) if size is None else size
        p.paste_resized_clipped(
            source,
            pos,
            resolved_size,
            (0, 0, *self.badge_size),
            blend="paste_lerp",
        )

    def _add_frame(self, p: Painter, level: int | None = None) -> None:
        frame = self.images.get("frame_img")
        if frame is None:
            return
        self._paste_overlay(p, frame, (8, 0) if self.rqd.honor_rarity == "low" else (0, 0))
        if self.rqd.honor_type != "birthday":
            return
        icon = self.images.get("frame_degree_level_img")
        if icon is None or not level:
            return
        w, h = self.badge_size
        sz = 18
        for i in range(level):
            self._paste_overlay(p, icon, (int(w / 2 - sz * level / 2 + i * sz), h - sz), (sz, sz))

    def _add_lv_star(self, p: Painter, level: int) -> None:
        if level > 10:
            level = level - 10
        lv_img = self.images.get("lv_img")
        lv6_img = self.images.get("lv6_img")
        if lv_img is not None:
            for i in range(0, min(level, 5)):
                self._paste_overlay(p, lv_img, (50 + 16 * i, 61))
        if lv6_img is not None:
            for i in range(5, level):
                self._paste_overlay(p, lv6_img, (50 + 16 * (i - 5), 61))

    def _add_fcap_lv(self, p: Painter) -> None:
        text = str(self.rqd.fc_or_ap_level or "")
        offset = 215 if self.rqd.is_main_honor else 37
        p.anchored_text(
            text,
            (offset + 50, FCAP_TEXT_TOP_Y),
            get_font_desc(DEFAULT_BOLD_FONT, FCAP_TEXT_SIZE),
            fill=WHITE,
            align="center",
            baseline="ascender",
        )

    # ---- branches ----

    def _draw_empty(self, p: Painter) -> None:
        self._paste_overlay(p, self.images["empty_honor"], (3, 3))

    def _draw_rank(self, p: Painter, base: ImageSource, group_type: str | None) -> None:
        rank_img = self.images.get("rank_img")
        if not rank_img:
            return
        if group_type == "rank_match":
            rank_pos = (190, 0) if self.rqd.is_main_honor else (17, 42)
        elif is_world_link_rank_style(group_type, self.rqd.rank_img_path):
            rank_pos = (0, 0)
        else:
            rank_pos = resolve_event_rank_position(_size_of(base), _size_of(rank_img), self.rqd.is_main_honor)
        self._paste_overlay(p, rank_img, rank_pos)

    def _draw_normal_level(self, p: Painter, group_type: str | None) -> None:
        if not honor_group_uses_scroll_level(group_type):
            if group_type in ("character", "achievement"):
                self._add_lv_star(p, self.rqd.honor_level)
            return
        scroll_img = self.images.get("scroll_img")
        if scroll_img is not None:
            self._paste_overlay(p, scroll_img, (215, 3) if self.rqd.is_main_honor else (37, 3))
        if group_type == "fc_ap" or scroll_img is not None:
            self._add_fcap_lv(p)

    def _draw_normal(self, p: Painter) -> None:
        rqd = self.rqd
        gtype = rqd.group_type
        base = self.images["honor_img"]
        p.paste_src(base, (0, 0))
        self._add_frame(p, rqd.honor_level)
        self._draw_rank(p, base, gtype)
        self._draw_normal_level(p, gtype)

    def _draw_bonds_op(self, p: Painter, op: FullResizeClipOp) -> None:
        source = self.images.get(op.source_key)
        assert source is not None, f"bonds plan referenced an absent source: {op.source_key}"
        p.paste_resized_clipped(
            source,
            op.destination_offset,
            op.full_resize_size,
            op.destination_clip,
            blend=op.blend,
            sampling=op.sampling,
        )

    def _draw_bonds(self, p: Painter) -> None:
        rqd = self.rqd
        c1_src = self.images.get("chara_icon_1")
        c2_src = self.images.get("chara_icon_2")
        mask_img = self.images.get("mask_img")
        plan = build_bonds_honor_plan(
            left_background_size=_size_of(self.images["bonds_bg"]),
            right_background_size=_size_of(self.images["bonds_bg2"]),
            chara_icon_1_size=None if c1_src is None else _size_of(c1_src),
            chara_icon_2_size=None if c2_src is None else _size_of(c2_src),
            is_main_honor=rqd.is_main_honor,
            honor_rarity=str(rqd.honor_rarity or ""),
            honor_level=int(rqd.honor_level or 0),
            mask_size=None if mask_img is None else _size_of(mask_img),
            frame_size=None if self.images.get("frame_img") is None else _size_of(self.images["frame_img"]),
            word_size=None if self.images.get("word_img") is None else _size_of(self.images["word_img"]),
            level_icon_size=None if self.images.get("lv_img") is None else _size_of(self.images["lv_img"]),
            level6_icon_size=None if self.images.get("lv6_img") is None else _size_of(self.images["lv6_img"]),
        )
        if plan.mask is not None:
            assert mask_img is not None
            p.push_mask(mask_img, plan.mask.destination_offset, plan.mask.full_resize_size)
        for op in plan.masked_ops:
            self._draw_bonds_op(p, op)
        if plan.mask is not None:
            p.pop_mask()
        for op in plan.post_mask_ops:
            self._draw_bonds_op(p, op)

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
