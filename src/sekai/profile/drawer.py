from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

from typing import TYPE_CHECKING

from src.assets.user_upload import get_user_upload_store, profile_bg_object_key
from src.core.image_payload import EncodedImagePayload
from src.sekai.base.draw import (
    BG_PADDING,
    DIFF_COLORS,
    PLAY_RESULT_COLORS,
    SEKAI_BLUE_BG,
    add_request_watermark,
    add_watermark,
    build_request_dt_watermark_text,
    roundrect_bg,
)
from src.sekai.base.font_metrics import get_layout_font as get_font
from src.sekai.base.paint_types import ADAPTIVE_WB, WHITE, get_font_desc
from src.sekai.base.text_layout import ascender_top_to_painter_y, get_text_size, ink_centered_text_offset_y
from src.settings import DEFAULT_BOLD_FONT, DEFAULT_FONT, DEFAULT_HEAVY_FONT

if TYPE_CHECKING:
    from src.sekai.base.painter import Painter
from src.sekai.base.image_source import MissingImageRef
from src.sekai.base.plot import (
    Canvas,
    CanvasImageBox,
    ColoredTextBox,
    Frame,
    Grid,
    HSplit,
    ImageBg,
    ImageBox,
    RoundClipFrame,
    RoundRectBg,
    Spacer,
    TextBox,
    TextStyle,
    VSplit,
    Widget,
    colored_text_box,
    parse_colored_text_segments,
)
from src.sekai.base.timezone import datetime_from_millis, request_now
from src.sekai.base.utils import (
    AssetImageRef,
    EncodedImageRef,
    ImageSource,
    build_rendered_image_cache_key,
    get_asset_image_ref,
    get_asset_image_refs,
    get_composed_image_cached,
    get_composed_image_disk_cached,
    get_encoded_image_ref,
    get_str_display_length,
    put_composed_image_cache,
    put_composed_image_disk_cache,
    run_in_pool,
    truncate,
)
from src.sekai.honor.drawer import (
    HonorRequest,
    build_full_honor_cache_key,
    build_honor_badge_canvas_from_request,
)
from src.sekai.skia_renderer.canvas import render_canvas_payload, skia_plot_enabled
from src.sekai.skia_renderer.card_common import rare_count
from src.settings import ASSETS_BASE_DIR

logger = logging.getLogger(__name__)


def format_info_panel_update_time(update_time, timezone_name: str | None) -> str:
    timezone_label = (timezone_name or "").strip()
    if not timezone_label and update_time.tzinfo is not None:
        timezone_label = update_time.tzname() or ""

    text = update_time.strftime("%m-%d %H:%M:%S")
    if timezone_label:
        text = f"{text} ({timezone_label})"
    # text += f" ({get_readable_datetime(update_time, show_original_time=False)})"
    return text


# =========================== 常量定义 =========================== #

CHARA_LIST = [
    ("miku", 21),
    ("rin", 22),
    ("len", 23),
    ("luka", 24),
    ("meiko", 25),
    ("kaito", 26),
    ("ick", 1),
    ("saki", 2),
    ("hnm", 3),
    ("shiho", 4),
    (None, None),
    (None, None),
    ("mnr", 5),
    ("hrk", 6),
    ("airi", 7),
    ("szk", 8),
    (None, None),
    (None, None),
    ("khn", 9),
    ("an", 10),
    ("akt", 11),
    ("toya", 12),
    (None, None),
    (None, None),
    ("tks", 13),
    ("emu", 14),
    ("nene", 15),
    ("rui", 16),
    (None, None),
    (None, None),
    ("knd", 17),
    ("mfy", 18),
    ("ena", 19),
    ("mzk", 20),
    (None, None),
    (None, None),
]

CHARA_ID2NICKNAME = {
    21: "miku",
    22: "rin",
    23: "len",
    24: "luka",
    25: "meiko",
    26: "kaito",
    1: "ick",
    2: "saki",
    3: "hnm",
    4: "shiho",
    5: "mnr",
    6: "hrk",
    7: "airi",
    8: "szk",
    9: "khn",
    10: "an",
    11: "akt",
    12: "toya",
    13: "tks",
    14: "emu",
    15: "nene",
    16: "rui",
    17: "knd",
    18: "mfy",
    19: "ena",
    20: "mzk",
}

# =========================== 从.model导入数据类型 =========================== #

from .model import (
    BasicProfile,
    CardFullThumbnailRequest,
    CharacterRank,
    MultiLiveTopScoreCount,
    MusicClearCount,
    ProfileBgSettings,
    ProfileCardRequest,
    ProfileDataSource,
    ProfileRequest,
    SoloLiveRank,
)


@dataclass(slots=True)
class _ProfileLayoutContext:
    request: ProfileRequest
    profile: BasicProfile
    avatar_img: ImageSource
    ui_bg: RoundRectBg
    pcards: list[CardFullThumbnailRequest]
    honors: list[HonorRequest]
    diff_count: dict[str, dict[str, int]]
    character_rank: dict[int, int]
    solo_live: SoloLiveRank | None
    multi_live: MultiLiveTopScoreCount | None


@dataclass(slots=True)
class CardFullThumbnailLayers:
    """Header-only layer refs for one card thumbnail (placeholder PIL image when an
    asset is missing). Load with :func:`get_card_full_thumbnail_layers` before entering
    layout ``with`` blocks; render with :class:`CardFullThumbnailBox`."""

    rqd: CardFullThumbnailRequest
    base: AssetImageRef | Image.Image
    rare: AssetImageRef | Image.Image
    frame: AssetImageRef | Image.Image | None = None
    rank: AssetImageRef | Image.Image | None = None
    attr: AssetImageRef | Image.Image | None = None


async def get_card_full_thumbnail_layers(rqd: CardFullThumbnailRequest) -> CardFullThumbnailLayers:
    rare_img_path = rqd.birthday_icon_path if rqd.rare == "rarity_birthday" else rqd.rare_img_path
    keys = ["base", "rare"]
    paths = [rqd.card_thumbnail_path, rare_img_path]
    if rqd.frame_img_path:
        keys.append("frame")
        paths.append(rqd.frame_img_path)
    if rqd.is_pcard and rqd.train_rank and rqd.train_rank_img_path:
        keys.append("rank")
        paths.append(rqd.train_rank_img_path)
    if rqd.attr_img_path:
        keys.append("attr")
        paths.append(rqd.attr_img_path)
    loaded = dict(zip(keys, await get_asset_image_refs(ASSETS_BASE_DIR, paths)))
    return CardFullThumbnailLayers(
        rqd=rqd,
        base=loaded["base"],
        rare=loaded["rare"],
        frame=loaded.get("frame"),
        rank=loaded.get("rank"),
        attr=loaded.get("attr"),
    )


class CardFullThumbnailBox(ImageBox):
    """Card thumbnail composed natively by whichever backend draws the tree.

    Layer recipe mirrors the legacy Pillow pre-composition (base art → pcard level
    bar/text → frame → train rank → attribute icon → rarity stars, clipped to
    10px-radius rounded corners at art scale), drawn through Painter primitives so
    the Skia path emits asset paths straight into the IR and the Pillow fallback
    decodes the same layers on demand. Constants are in base-art pixels and scale
    with the display size, matching the legacy compose-then-resize output."""

    def __init__(
        self,
        layers: CardFullThumbnailLayers,
        size=None,
        image_size_mode=None,
        shadow=False,
        shadow_width=6,
        shadow_alpha=0.6,
        sampling=None,
    ) -> None:
        super().__init__(layers.base, image_size_mode=image_size_mode, size=size, sampling=sampling)
        self.layers = layers
        self.thumb_shadow = shadow
        self.thumb_shadow_width = shadow_width
        self.thumb_shadow_alpha = shadow_alpha
        self.prefetch_image_sources = [
            layer for layer in (layers.base, layers.rare, layers.frame, layers.rank, layers.attr) if layer is not None
        ]

    def _draw_content(self, p: Painter) -> None:
        w, h = self._get_content_size()
        layers, rqd = self.layers, self.layers.rqd
        art_w, art_h = self.image.size
        sx, sy = w / art_w, h / art_h
        radius = max(1, round(10 * sy))
        if self.thumb_shadow:
            p.shadow_roundrect((0, 0), (w, h), radius, self.thumb_shadow_width, self.thumb_shadow_alpha)
        p.push_clip_roundrect((0, 0), (w, h), radius)
        p.paste(self.image, (0, 0), (w, h), sampling=self.sampling)
        pcard = rqd.is_pcard
        if pcard:
            bar_h = round(24 * sy)
            p.rect((0, h - bar_h), (w, bar_h), fill=(70, 70, 100, 255))
            text = rqd.custom_text or f"Lv.{rqd.level}"
            font_size = max(1, round(20 * sy))
            font = get_font_desc(DEFAULT_BOLD_FONT, font_size)
            y = ascender_top_to_painter_y(DEFAULT_BOLD_FONT, font_size, h - round(31 * sy))
            p.text(text, (round(6 * sx), y), font=font, fill=WHITE)
        # The overlays go through paste_with_alpha_blend, not paste: Pillow's paste(im, pos, im)
        # lerps the DESTINATION alpha toward the layer's, so an anti-aliased frame/star edge
        # would leave the composed thumbnail translucent there and the page background (and the
        # drop shadow underneath) would bleed through as a halo. The legacy composer got away
        # with plain pastes because it finished with img.putalpha(mask), hard-resetting alpha to
        # opaque; the clip only multiplies alpha, so it cannot undo that. alpha_composite keeps
        # dst alpha at 255 and is what the Skia backend already does for both paste variants.
        if layers.frame is not None:
            p.paste_with_alpha_blend(layers.frame, (0, 0), (w, h), sampling=self.sampling)
        if pcard and rqd.train_rank and layers.rank is not None:
            rank_w, rank_h = max(1, round(w * 0.35)), max(1, round(h * 0.35))
            p.paste_with_alpha_blend(layers.rank, (w - rank_w, h - rank_h), (rank_w, rank_h), sampling=self.sampling)
        if layers.attr is not None:
            p.paste_with_alpha_blend(
                layers.attr, (round(sx), 0), (max(1, round(w * 0.22)), max(1, round(h * 0.25))), sampling=self.sampling
            )
        rare_scale = 0.17 if not pcard else 0.15
        rare_w, rare_h = max(1, round(w * rare_scale)), max(1, round(h * rare_scale))
        hoffset, voffset = round(6 * sx), round((24 if pcard else 6) * sy)
        for i in range(rare_count(rqd.rare)):
            p.paste_with_alpha_blend(
                layers.rare, (hoffset + rare_w * i, h - rare_h - voffset), (rare_w, rare_h), sampling=self.sampling
            )
        p.pop_clip()


# ---------------------------------------------------------------------------------------------
# Player frames (头像框/プレイヤーフレーム)
#
# The client never frames a bare avatar. ``PlayerFrameSinglePartsView`` /
# ``PlayerFrameCombination6PartsView`` decorate a whole player cell: the base sprite is a sliced
# (9-slice) image stretched over the cell, and every ornament is ``SetNativeSize``-d (1 sprite px =
# 1 UI unit) and pinned to a cell corner whose pivot equals its anchor. Bundles are
# ``player_frame/{group}/{frameId}/{cell}`` (single) and ``player_frame/{group}/{frameId}/{partId}/{cell}``
# (combination, one bundle per character-coloured part); ``cell`` is ``horizontal`` for list rows
# (friend list, event / rank-live ranking: 1542×146), ``vertical`` for the multi-live player cell
# (340×748) and ``landscape`` for the landscape result cell. The RectTransforms below are read from
# the 6.8.1/7.0 ``resources.assets`` prefabs; the sprite→slot table from
# ``PlayerFrameSinglePartsView.Setup`` and ``PlayerFrameCombination6PartsView.LoadPartSprites*``.
# ---------------------------------------------------------------------------------------------

# slot -> (anchor_x, anchor_y_from_top, offset_x, offset_y) in UI units; offset y grows upwards like Unity.
_FRAME_SLOTS: dict[str, dict[str, tuple[float, float, float, float]]] = {
    # FriendListCell / EventRankingCell / RankLiveRankingCell ... (PlayerFrameListCell)
    "horizontal": {
        "tl": (0, 0, -8, 16),
        "tr": (1, 0, 8, 16),
        "tc": (0, 0, 50, 16),  # anchored top-left; x = _framePartsTopCenterXByFriend (ByRanking = 225)
        "bl": (0, 1, -8, -8),
        "br": (1, 1, 8, -8),
    },
    # MultiLiveMatchingRoomPlayerCell / PlayerFrameSetting preview (PlayerFrameCell)
    "vertical": {
        "tl": (0, 0, -16, 32),
        "tr": (1, 0, 16, 32),
        "tc": (0.5, 0, 0, 32),
        "bl": (0, 1, -16, -8),
        "br": (1, 1, 16, -8),
    },
}
# Reference cell sizes in UI units (prefab sizeDelta of the PlayerFrame*Cell roots).
FRAME_REFERENCE_CELL = {"horizontal": (1542, 146), "vertical": (340, 748)}

# (part number, sprite name, slot) per frame kind, in the prefab's sibling (= draw) order; part 1
# also carries frame_base, which sits under everything.
_FRAME_SPRITES: dict[str, dict[str, tuple[tuple[int, str, str], ...]]] = {
    "single": {
        "horizontal": (
            (1, "lefttop", "tl"),
            (1, "righttop", "tr"),
            (1, "rightbottom", "br"),
            (1, "leftbottom", "bl"),
            (1, "centertop", "tc"),
        ),
        "vertical": (
            (1, "lefttop", "tl"),
            (1, "righttop", "tr"),
            (1, "centertop", "tc"),
            (1, "rightbottom", "br"),
            (1, "leftbottom", "bl"),
        ),
    },
    "combination": {
        "horizontal": (
            (5, "parts5_bottom", "br"),
            (3, "parts3_bottom", "bl"),
            (4, "parts4_top", "tr"),
            (2, "parts2_top", "tl"),
            (1, "parts1_top", "tl"),
            (6, "parts6_top", "tr"),
            (6, "parts6_bottom", "br"),
            (1, "parts1_bottom", "bl"),
            (1, "parts1_center", "tc"),
        ),
        "vertical": (
            (5, "parts5_right", "br"),
            (4, "parts4_left", "bl"),
            (3, "parts3_right", "tr"),
            (2, "parts2_left", "tl"),
            (1, "parts1_left", "tl"),
            (1, "parts1_right", "tr"),
            (1, "parts1_center", "tc"),
            (6, "parts6_right", "br"),
            (6, "parts6_left", "bl"),
        ),
    },
}
# Fields of the (vertical) PlayerFramePaths Cloud sends, by the part bundle they come from.
_FRAME_PART_FIELDS = {
    "single": {1: "base"},
    "combination": {
        1: "base",
        2: "side_left_top",
        3: "side_right_top",
        4: "side_left_bottom",
        5: "side_right_bottom",
        6: "leftbottom",
    },
}
# The sprites' slice borders are not exported with the PNGs; every base is a rounded rectangle whose
# curve ends at ~24% of its edge (32/132 vertical, 6-10/60 horizontal), so 38% always holds the whole
# corner and leaves only straight edge to stretch.
_FRAME_BASE_BORDER = 0.38


def _frame_part_dirs(frame_paths) -> dict[int, str] | None:
    """Bundle directory of each part, recovered from the per-sprite paths (``…/<bundle>/<cell>/frame_x.png``)."""
    kind = getattr(frame_paths, "frame_type", None) or "single"
    fields = _FRAME_PART_FIELDS.get(kind)
    if fields is None:
        return None
    dirs: dict[int, str] = {}
    for part, field in fields.items():
        path = getattr(frame_paths, field, None)
        if not path:
            return None
        bundle_dir, _, _ = path.rpartition("/")  # …/<bundle>/<cell>
        bundle_dir, _, cell = bundle_dir.rpartition("/")
        if not bundle_dir or cell not in ("vertical", "horizontal", "landscape"):
            return None
        dirs[part] = bundle_dir
    return dirs


@dataclass(slots=True)
class PlayerFrameLayers:
    cell: str
    base: ImageSource
    ornaments: list[tuple[ImageSource, str]]  # (sprite, slot) in the client's draw order


async def get_player_frame_layers(frame_paths, cell: str = "horizontal") -> PlayerFrameLayers | None:
    """Sprites for ``cell`` (``horizontal`` / ``vertical``); ``None`` unless every sprite is readable."""
    if frame_paths is None or cell not in _FRAME_SLOTS:
        return None
    kind = getattr(frame_paths, "frame_type", None) or "single"
    explicit = getattr(frame_paths, "horizontal", None) if cell == "horizontal" else None
    if explicit is not None:
        sprites = _FRAME_SPRITES["single"][cell]
        paths = [explicit.base, *(getattr(explicit, name) for _, name, _ in sprites)]
    else:
        dirs = _frame_part_dirs(frame_paths)
        if dirs is None:
            return None
        sprites = _FRAME_SPRITES[kind][cell]
        paths = [
            f"{dirs[1]}/{cell}/frame_base.png",
            *(f"{dirs[part]}/{cell}/frame_{name}.png" for part, name, _ in sprites),
        ]
    try:
        sources = await asyncio.gather(
            *(get_asset_image_ref(ASSETS_BASE_DIR, path, on_missing="raise") for path in paths)
        )
    except (OSError, ValueError):
        return None
    if any(isinstance(image, MissingImageRef) for image in sources):
        return None
    return PlayerFrameLayers(
        cell, sources[0], [(image, slot) for image, (_, _, slot) in zip(sources[1:], sprites, strict=True)]
    )


class PlayerFrameBox(Widget):
    """A player frame laid over a ``size`` rect exactly as the client lays it over a player cell.

    ``scale`` converts UI units to pixels: ornaments keep their native aspect at ``sprite * scale``,
    offsets scale with them and the base is 9-sliced over the rect. When the rect is narrower than
    the client cell the left- and right-anchored strips would overlap; ``split`` crops them at the
    vertical centre line instead (the client never needs to). ``outset`` grows the framed rect past
    the widget box on every side (the widget still lays out at ``size``). Every pixel position and
    size is integral, so the Pillow fallback never sees fractional geometry.
    """

    def __init__(
        self,
        layers: PlayerFrameLayers,
        size: tuple[int, int],
        scale: float,
        *,
        split: bool = True,
        outset: float = 0,
    ) -> None:
        super().__init__()
        self.layers = layers
        self.box_size = (max(1, int(size[0])), max(1, int(size[1])))
        self.outset = outset
        self.frame_size = (max(1, round(size[0] + 2 * outset)), max(1, round(size[1] + 2 * outset)))
        self.frame_scale = scale
        self.split = split
        self.prefetch_image_sources = [layers.base, *(image for image, _ in layers.ornaments)]
        self.set_allow_draw_outside(True)

    def _get_content_size(self) -> tuple[int, int]:
        return self.box_size

    def _draw_base(self, p: Painter) -> None:
        w, h = self.frame_size
        base = self.layers.base
        bw, bh = base.size
        src = max(1, min(round(min(bw, bh) * _FRAME_BASE_BORDER), (bw - 1) // 2, (bh - 1) // 2))
        dst = max(1, min(round(src * self.frame_scale), w // 2, h // 2))
        xs, ys = (0, src, bw - src, bw), (0, src, bh - src, bh)
        o = -self.outset
        dx = tuple(round(o + v) for v in (0, dst, w - dst, w))
        dy = tuple(round(o + v) for v in (0, dst, h - dst, h))
        for j in range(3):
            for i in range(3):
                if dx[i + 1] <= dx[i] or dy[j + 1] <= dy[j]:
                    continue
                p.paste_with_alpha_blend(
                    base,
                    (dx[i], dy[j]),
                    (dx[i + 1] - dx[i], dy[j + 1] - dy[j]),
                    src_rect=(xs[i], ys[j], xs[i + 1], ys[j + 1]),
                )

    def _draw_content(self, p: Painter) -> None:
        w, h = self.frame_size
        s = self.frame_scale
        slots = _FRAME_SLOTS[self.layers.cell]
        self._draw_base(p)
        o = -self.outset
        mid = o + w / 2
        for image, slot in self.layers.ornaments:
            ax, ay, ox, oy = slots[slot]
            iw, ih = image.size
            dw, dh = max(1, round(iw * s)), max(1, round(ih * s))
            x = round(o + ax * w + ox * s - ax * dw)
            y = round(o + h - dh - oy * s) if ay else round(o - oy * s)
            src = (0, 0, iw, ih)
            if self.split and slot != "tc":
                # left-anchored strips stop at the centre line, right-anchored ones start there
                if ax == 0 and x + dw > mid:
                    keep = max(0, round(mid) - x)
                    src, dw = (0, 0, max(1, round(keep / s)), ih), keep
                elif ax == 1 and x < mid:
                    cut = round(mid) - x
                    src, x, dw = (min(iw - 1, round(cut / s)), 0, iw, ih), round(mid), dw - cut
                if dw <= 0:
                    continue
            p.paste_with_alpha_blend(image, (x, y), (dw, dh), src_rect=src if src != (0, 0, iw, ih) else None)


def frame_scale_for(layers: PlayerFrameLayers, size: tuple[int, int]) -> float:
    """UI units -> px for a frame around ``size``.

    The client's reference cell height maps onto ``size``'s height, but never so large that the
    left- and right-anchored strips meet: the client cell is always wide enough to leave a plain
    stretch of base between them, and every combination part must stay visible.
    """
    w, h = size
    ref_h = FRAME_REFERENCE_CELL[layers.cell][1]
    slots = _FRAME_SLOTS[layers.cell]
    widest = {0: 0.0, 1: 0.0}
    for image, slot in layers.ornaments:
        ax, _, ox, _ = slots[slot]
        if slot != "tc" and ax in widest:
            widest[ax] = max(widest[ax], image.size[0] + ox * (1 if ax == 0 else -1))
    span = widest[0] + widest[1]
    return min(h / ref_h, w / span) if span > 0 else h / ref_h


async def wrap_with_player_frame(
    widget: Widget,
    frame_paths,
    *,
    cell: str = "horizontal",
    scale: float | None = None,
    scale_reference: tuple[int, int] | None = None,
    split: bool = True,
) -> Widget:
    """``widget`` with the equipped frame drawn over its whole box; ``widget`` itself when there is none.

    The frame is scaled for ``widget``'s own box unless ``scale`` fixes it or ``scale_reference``
    names another box to size it for, so differently shaped panels can share one frame thickness.
    """
    layers = await get_player_frame_layers(frame_paths, cell) if frame_paths else None
    if layers is None:
        return widget
    size = widget._get_self_size()
    if scale is not None:
        s = scale
    else:
        s = frame_scale_for(layers, scale_reference or size)
    # Widgets attach to the active container on construction; build the wrapper detached and put
    # it where ``widget`` was so neither is drawn twice.
    ret = Frame()
    if ret.parent is not None:
        ret.parent.items.remove(ret)
        ret.set_parent(None)
    parent = widget.parent
    ret.set_content_align("lt").set_allow_draw_outside(True)
    with ret:
        PlayerFrameBox(layers, size, s, split=split)
    ret.items.insert(0, widget)
    if parent is not None and widget in getattr(parent, "items", ()):
        parent.items[parent.items.index(widget)] = ret
        ret.set_parent(parent)
    widget.set_parent(ret)
    return ret


def process_hide_uid(is_hide_uid: bool, uid: str, keep: int = 0) -> str:
    if is_hide_uid:
        if keep:
            return "*" * (16 - keep) + str(uid)[-keep:]
        return "*" * 16
    return uid


def _build_profile_diff_count(music_difficulty_count: list[MusicClearCount]) -> dict[str, dict[str, int]]:
    diff_count = {diff: {"clear": 0, "fc": 0, "ap": 0} for diff in DIFF_COLORS.keys()}
    for count in music_difficulty_count:
        if count.difficulty in diff_count:
            diff_count[count.difficulty] = {"clear": count.clear, "fc": count.fc, "ap": count.ap}
    return diff_count


def _build_profile_character_rank_lookup(character_ranks: list[CharacterRank]) -> dict[int, int]:
    rank_lookup = {cid: 1 for _, cid in CHARA_LIST if cid is not None}
    for crank in character_ranks:
        rank_lookup[crank.character_id] = crank.rank
    return rank_lookup


async def _render_profile_widget_image(widget: Widget, *, scale: float = 1.0) -> Image.Image:
    with Canvas().set_padding(0) as canvas:
        canvas.add_item(widget)
    return await canvas.get_img(scale)


async def _build_cached_profile_module_image(
    namespace: str,
    request_payload,
    build_widget,
    *,
    asset_signatures: dict | None = None,
    extra: dict | None = None,
    scale: float = 1.0,
) -> Image.Image:
    cache_key = build_rendered_image_cache_key(
        namespace,
        request_payload,
        asset_signatures=asset_signatures,
        extra=extra,
    )
    cached = get_composed_image_cached(cache_key)
    if cached is not None:
        return cached
    disk_cached = get_composed_image_disk_cached(namespace, cache_key)
    if disk_cached is not None:
        put_composed_image_cache(cache_key, disk_cached)
        return disk_cached

    widget = await build_widget()
    image = await _render_profile_widget_image(widget, scale=scale)
    put_composed_image_cache(cache_key, image)
    put_composed_image_disk_cache(namespace, cache_key, image)
    return image


def _build_cached_profile_module_widget(image: Image.Image) -> Widget:
    return ImageBox(image, image_size_mode="original", use_alpha_blend=True)


async def _build_profile_avatar_module(ctx: _ProfileLayoutContext) -> Widget:
    # The equipped frame goes around the whole info panel (see _frame_profile_info_panel), not the avatar.
    return ImageBox(ctx.avatar_img, size=(128, 128), use_alpha_blend=False)


# The info panel wears the frame at the thickness the profile card (the "info panel" atop other
# pages) gives it: a card-sized box (_CARD_W wide, a two-source card tall) decides the scale,
# whatever the panel's own shape.
_PROFILE_PANEL_FRAME_REFERENCE_H = 126


async def _frame_profile_info_panel(ctx: _ProfileLayoutContext, panel: Widget) -> Widget:
    """Frame the placed info panel in place, the way the profile card frames its card."""
    profile = ctx.request.profile
    frame_paths = ctx.request.frame_paths or profile.frame_paths
    if not profile.has_frame or frame_paths is None:
        return panel
    return await wrap_with_player_frame(
        panel, frame_paths, cell="horizontal", scale_reference=(_CARD_W, _PROFILE_PANEL_FRAME_REFERENCE_H)
    )


def _build_profile_identity_text_module(ctx: _ProfileLayoutContext) -> Widget:
    profile = ctx.profile
    request = ctx.request
    text_col = VSplit().set_content_align("c").set_item_align("l").set_sep(16)
    text_col.add_item(
        colored_text_box(
            truncate(request.profile.nickname, 64),
            TextStyle(font=DEFAULT_BOLD_FONT, size=32, color=ADAPTIVE_WB, use_shadow=True, shadow_offset=2),
        )
    )
    text_col.add_item(
        TextBox(
            f"{profile.region.upper()}: {process_hide_uid(profile.is_hide_uid, profile.id, keep=6)}",
            TextStyle(font=DEFAULT_FONT, size=20, color=ADAPTIVE_WB),
        )
    )
    return text_col


async def _build_profile_rank_badge_module(ctx: _ProfileLayoutContext) -> Widget:
    lv_rank_bg = await get_asset_image_ref(ASSETS_BASE_DIR, ctx.request.lv_rank_bg_path)
    badge_w = 180
    badge_h = max(1, int(lv_rank_bg.size[1] * badge_w / lv_rank_bg.size[0]))
    number_box_x = 104
    number_box_w = max(48, badge_w - number_box_x - 10)

    badge = Frame().set_size((badge_w, badge_h))
    badge.add_item(ImageBox(lv_rank_bg, size=(badge_w, badge_h)))
    badge.add_item(
        TextBox(
            f"{ctx.request.rank}",
            TextStyle(font=DEFAULT_FONT, size=30, color=WHITE),
        )
        .set_size((number_box_w, badge_h))
        .set_padding(0)
        .set_wrap(False)
        .set_content_align("c")
        .set_offset((number_box_x, 0))
    )
    return badge


async def _build_profile_identity_module(ctx: _ProfileLayoutContext) -> Widget:
    async def _build_identity_widget() -> Widget:
        avatar_module, rank_badge_module = await asyncio.gather(
            _build_profile_avatar_module(ctx),
            _build_profile_rank_badge_module(ctx),
        )
        root = HSplit().set_content_align("c").set_item_align("c").set_sep(32).set_padding((32, 0))
        root.add_item(avatar_module)
        text_col = _build_profile_identity_text_module(ctx)
        text_col.add_item(rank_badge_module)
        root.add_item(text_col)
        return root

    # Adaptive text colors must be evaluated on the final painted background.
    # Rendering this module through the disk/memory widget cache bakes it on a
    # transparent canvas first, which turns the nickname/ID text white.
    return await _build_identity_widget()


async def _build_profile_twitter_module(ctx: _ProfileLayoutContext) -> Widget:
    root = Frame().set_content_align("l").set_w(450)
    root.add_item(
        TextBox(
            "        @ " + ctx.request.twitter_id,
            TextStyle(font=DEFAULT_FONT, size=20, color=ADAPTIVE_WB),
            line_count=1,
        )
        .set_wrap(False)
        .set_bg(ctx.ui_bg)
        .set_line_sep(2)
        .set_padding(10)
        .set_w(300)
        .set_content_align("l")
    )
    x_icon = await get_asset_image_ref(ASSETS_BASE_DIR, ctx.request.x_icon_path)
    root.add_item(ImageBox(x_icon, image_size_mode="fill", size=(24, 24), sampling="linear").set_offset((16, 0)))
    return root


def _build_profile_word_module(ctx: _ProfileLayoutContext) -> Widget:
    return (
        ColoredTextBox(
            ctx.request.word,
            TextStyle(font=DEFAULT_FONT, size=20, color=ADAPTIVE_WB),
            line_count=3,
        )
        .set_wrap(True)
        .set_bg(ctx.ui_bg)
        .set_line_sep(2)
        .set_padding((18, 16))
        .set_w(450)
    )


async def _build_profile_honor_module(ctx: _ProfileLayoutContext) -> Widget:
    root = HSplit().set_content_align("c").set_item_align("c").set_sep(8).set_padding((16, 0))
    honor_canvases = await asyncio.gather(
        *[build_honor_badge_canvas_from_request(honor) for honor in ctx.honors],
        return_exceptions=True,
    )
    for honor, honor_canvas in zip(ctx.honors, honor_canvases, strict=True):
        if isinstance(honor_canvas, Exception):
            logger.warning("skip broken honor asset in profile image: %s", honor_canvas)
            continue
        if honor_canvas is not None:
            try:
                cache_key = build_full_honor_cache_key(honor)
            except Exception as exc:
                logger.warning("skip honor with an invalid composed-cache key: %s", exc)
                continue
            root.add_item(
                CanvasImageBox(
                    honor_canvas,
                    size=(None, 48),
                    shadow=True,
                    sampling="catmull_rom",
                    cache_key=cache_key,
                    require_asset_backed=True,
                    skip_on_error=True,
                )
            )
    return root


async def _build_profile_cards_module(ctx: _ProfileLayoutContext) -> Widget:
    root = HSplit().set_content_align("c").set_item_align("c").set_sep(6).set_padding((16, 0))
    _t0 = time.perf_counter()
    card_layers = await asyncio.gather(*[get_card_full_thumbnail_layers(card) for card in ctx.pcards])
    logger.debug("[perf] draw_main card_imgs %d: %.3fs", len(ctx.pcards), time.perf_counter() - _t0)
    for layers in card_layers:
        root.add_item(CardFullThumbnailBox(layers, size=(90, 90), image_size_mode="fill", shadow=True))
    return root


async def _build_profile_info_panel(ctx: _ProfileLayoutContext) -> Widget:
    identity_module, twitter_module, honor_module, cards_module = await asyncio.gather(
        _build_profile_identity_module(ctx),
        _build_profile_twitter_module(ctx),
        _build_profile_honor_module(ctx),
        _build_profile_cards_module(ctx),
    )

    root = VSplit().set_bg(ctx.ui_bg).set_content_align("c").set_item_align("c").set_sep(32).set_padding((32, 35))
    root.add_item(identity_module)
    root.add_item(twitter_module)
    root.add_item(_build_profile_word_module(ctx))
    root.add_item(honor_module)
    root.add_item(cards_module)
    return root


async def _build_profile_play_icon_module(ctx: _ProfileLayoutContext) -> Widget:
    gh = 25
    vs = 12
    icon_column = VSplit().set_sep(vs)
    icon_column.add_item(Spacer(gh, gh))
    _t0 = time.perf_counter()
    icon_clear, icon_fc, icon_ap = await asyncio.gather(
        get_asset_image_ref(ASSETS_BASE_DIR, ctx.request.icon_clear_path),
        get_asset_image_ref(ASSETS_BASE_DIR, ctx.request.icon_fc_path),
        get_asset_image_ref(ASSETS_BASE_DIR, ctx.request.icon_ap_path),
    )
    logger.debug("[perf] draw_play play icons 3: %.3fs", time.perf_counter() - _t0)
    icon_column.add_item(ImageBox(icon_clear, size=(gh, gh)))
    icon_column.add_item(ImageBox(icon_fc, size=(gh, gh)))
    icon_column.add_item(ImageBox(icon_ap, size=(gh, gh)))
    return icon_column


def _build_profile_play_grid_module(ctx: _ProfileLayoutContext) -> Widget:
    hs, vs, gw, gh = 8, 12, 90, 25
    grid = Grid(col_count=6).set_sep(h_sep=hs, v_sep=vs)
    for diff, color in DIFF_COLORS.items():
        grid.add_item(
            TextBox(diff.upper(), TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=WHITE))
            .set_bg(RoundRectBg(fill=color, radius=3))
            .set_size((gw, gh))
            .set_content_align("c")
        )

    for result_name in ["clear", "fc", "ap"]:
        for column, diff in enumerate(DIFF_COLORS.keys()):
            bg_color = (255, 255, 255, 150) if column % 2 == 0 else (255, 255, 255, 100)
            count = ctx.diff_count[diff][result_name]
            grid.add_item(
                TextBox(
                    str(count),
                    TextStyle(
                        DEFAULT_FONT,
                        20,
                        PLAY_RESULT_COLORS["not_clear"],
                        use_shadow=True,
                        shadow_color=PLAY_RESULT_COLORS[result_name],
                        shadow_offset=1,
                    ),
                )
                .set_bg(RoundRectBg(fill=bg_color, radius=3))
                .set_size((gw, gh))
                .set_content_align("c")
            )
    return grid


async def _build_profile_play_content_module(ctx: _ProfileLayoutContext) -> Widget:
    icon_module = await _build_profile_play_icon_module(ctx)
    root = HSplit().set_content_align("c").set_item_align("t").set_sep(12)
    root.add_item(icon_module)
    root.add_item(_build_profile_play_grid_module(ctx))
    return root


async def _build_profile_play_panel(ctx: _ProfileLayoutContext) -> Widget:
    root = HSplit().set_content_align("c").set_item_align("t").set_sep(12).set_bg(ctx.ui_bg).set_padding(32)
    root.add_item(await _build_profile_play_content_module(ctx))
    return root


async def _preload_profile_chara_icons(ctx: _ProfileLayoutContext) -> dict[str, ImageSource]:
    chara_map = ctx.request.chara_rank_icon_path_map
    chara_paths: dict[str, str] = {}
    for chara, cid in CHARA_LIST:
        if chara is None:
            continue
        path = chara_map.get(cid) or chara_map.get(str(cid))
        if path and path not in chara_paths:
            chara_paths[path] = path
    if ctx.solo_live is not None:
        solo_path = chara_map.get(ctx.solo_live.character_id) or chara_map.get(str(ctx.solo_live.character_id))
        if solo_path and solo_path not in chara_paths:
            chara_paths[solo_path] = solo_path
    ordered_paths = list(chara_paths.keys())
    _t0 = time.perf_counter()
    images = (
        await asyncio.gather(*[get_asset_image_ref(ASSETS_BASE_DIR, path) for path in ordered_paths])
        if ordered_paths
        else []
    )
    logger.debug("[perf] draw_chara chara icons %d: %.3fs", len(ordered_paths), time.perf_counter() - _t0)
    return dict(zip(ordered_paths, images))


def _build_profile_stats_badge(text: str, *, font_size: int = 18, width: int | None = None) -> Widget:
    badge = (
        TextBox(
            text,
            TextStyle(font=DEFAULT_FONT, size=font_size, color=(50, 50, 50, 255)),
        )
        .set_bg(roundrect_bg(radius=6, alpha=80))
        .set_padding((10, 7))
        .set_content_align("c")
    )
    if width is not None:
        badge.set_w(width)
    return badge


def _profile_stats_badge_width(text: str, *, font_size: int = 18) -> int:
    return get_text_size(get_font(DEFAULT_FONT, font_size), text)[0] + 20


def _build_profile_character_grid_module(
    ctx: _ProfileLayoutContext,
    chara_icon_cache: dict[str, ImageSource],
) -> Widget:
    chara_map = ctx.request.chara_rank_icon_path_map
    grid = Grid(col_count=6).set_sep(h_sep=8, v_sep=7).set_padding(32)
    for chara, cid in CHARA_LIST:
        if chara is None:
            grid.add_item(Spacer(96, 48))
            continue
        rank = ctx.character_rank[cid]
        c_rank_path = chara_map.get(cid) or chara_map.get(str(cid))
        if not c_rank_path:
            grid.add_item(Spacer(96, 48))
            continue

        chara_frame = Frame().set_size((96, 48))
        chara_frame.add_item(ImageBox(chara_icon_cache[c_rank_path], size=(96, 48), use_alpha_blend=True))
        chara_frame.add_item(
            TextBox(str(rank), TextStyle(font=DEFAULT_FONT, size=20, color=(40, 40, 40, 255)))
            .set_size((60, 48))
            .set_content_align("c")
            .set_offset((36, 4))
        )
        grid.add_item(chara_frame)
    return grid


def _build_profile_multi_live_module(
    side_panel_w: int | None,
    multi_live: MultiLiveTopScoreCount,
    stats_w: int,
) -> Widget:
    module = VSplit().set_content_align("c").set_item_align("c").set_padding((32, 16)).set_sep(10).set_offset((0, -16))
    if side_panel_w is not None:
        module.set_w(side_panel_w)
    module.add_item(_build_profile_stats_badge("MULTI LIVE"))
    module.add_item(_build_profile_stats_badge(f"MVP {multi_live.mvp}次", width=stats_w))
    module.add_item(_build_profile_stats_badge(f"SUPERSTAR {multi_live.super_star}次", font_size=17, width=stats_w))
    return module


def _build_profile_solo_live_module(
    ctx: _ProfileLayoutContext,
    chara_icon_cache: dict[str, ImageSource],
    side_panel_w: int | None,
    stats_score_w: int | None,
    solo_live_offset_y: int,
) -> Widget:
    solo_live = ctx.solo_live
    chara_map = ctx.request.chara_rank_icon_path_map

    module = VSplit().set_content_align("c").set_item_align("c").set_padding((32, 64)).set_sep(12)
    if side_panel_w is not None:
        module.set_w(side_panel_w)
    if solo_live_offset_y != 0:
        module.set_offset((0, solo_live_offset_y))

    module.add_item(_build_profile_stats_badge("CHALLENGE LIVE"))
    chara_frame = Frame()
    c_rank_path = chara_map.get(solo_live.character_id) or chara_map.get(str(solo_live.character_id))
    if c_rank_path:
        chara_frame.add_item(ImageBox(chara_icon_cache[c_rank_path], size=(100, 50), use_alpha_blend=True))
    else:
        chara_frame.add_item(Spacer(100, 50))
    chara_frame.add_item(
        TextBox(
            str(solo_live.rank),
            TextStyle(font=DEFAULT_FONT, size=22, color=(40, 40, 40, 255)),
            overflow="clip",
        )
        .set_size((50, 50))
        .set_content_align("c")
        .set_offset((40, 5))
    )
    module.add_item(chara_frame)
    module.add_item(_build_profile_stats_badge(f"SCORE {solo_live.score}", font_size=18, width=stats_score_w))
    return module


async def _build_profile_growth_content_module(ctx: _ProfileLayoutContext) -> Widget:
    chara_icon_cache = await _preload_profile_chara_icons(ctx)
    root = Frame().set_content_align("rb")
    root.add_item(_build_profile_character_grid_module(ctx, chara_icon_cache))

    solo_live_score_w = None
    side_panel_w = None
    side_panel_content_w = 0

    if ctx.solo_live is not None:
        solo_live_content_w = max(
            _profile_stats_badge_width("CHALLENGE LIVE"),
            100,
            _profile_stats_badge_width(f"SCORE {ctx.solo_live.score}"),
        )
        solo_live_score_w = _profile_stats_badge_width(f"SCORE {ctx.solo_live.score}")
        side_panel_content_w = max(side_panel_content_w, solo_live_content_w)

    multi_live_widget = None
    if ctx.multi_live is not None:
        multi_live_stats_w = max(
            solo_live_score_w or 0,
            _profile_stats_badge_width(f"MVP {ctx.multi_live.mvp}次"),
            _profile_stats_badge_width(f"SUPERSTAR {ctx.multi_live.super_star}次", font_size=17),
        )
        multi_live_content_w = max(
            _profile_stats_badge_width("MULTI LIVE"),
            multi_live_stats_w,
        )
        side_panel_content_w = max(side_panel_content_w, multi_live_content_w)
        side_panel_w = side_panel_content_w + 64
        multi_live_widget = _build_profile_multi_live_module(side_panel_w, ctx.multi_live, multi_live_stats_w)
        root.add_item(multi_live_widget)
    elif side_panel_content_w > 0:
        side_panel_w = side_panel_content_w + 64

    if ctx.solo_live is not None:
        solo_live_offset_y = -16
        if multi_live_widget is not None:
            multi_live_widget_h = multi_live_widget._get_self_size()[1]
            solo_live_offset_y -= max(0, multi_live_widget_h - 16 + 12 - 64)
        root.add_item(
            _build_profile_solo_live_module(
                ctx,
                chara_icon_cache,
                side_panel_w,
                solo_live_score_w,
                solo_live_offset_y,
            )
        )

    return root


async def _build_profile_growth_panel(ctx: _ProfileLayoutContext) -> Widget:
    root = Frame().set_content_align("rb").set_bg(ctx.ui_bg)
    # The growth panel contains nested translucent badges. They need the real
    # destination background to preserve the intended alpha/glass appearance.
    root.add_item(await _build_profile_growth_content_module(ctx))
    return root


async def _build_profile_layout_modules(ctx: _ProfileLayoutContext) -> dict[str, Widget]:
    # Visible rounded panels are treated as the top-level profile modules so
    # future feature work can target one panel at a time.
    info_module, play_module, growth_module = await asyncio.gather(
        _build_profile_info_panel(ctx),
        _build_profile_play_panel(ctx),
        _build_profile_growth_panel(ctx),
    )
    return {
        "info": info_module,
        "play": play_module,
        "growth": growth_module,
    }


async def _load_profile_background(img_path: str) -> AssetImageRef | EncodedImageRef | None:
    """The user-uploaded background for `img_path`, or `None` for the default background.

    With `assets.user_upload.enabled`, a `user_upload/profile_bg/...` path (the key Cloud's ProfileBGStore writes
    to the `user-upload` bucket) is read from that store first and travels to the renderer as encoded bytes. A
    bucket miss or failure has already been logged by the store; it then tries today's local file when
    `local_fallback` is on. Any other path — and every path while the store is off — resolves under the assets
    root exactly as before — including a `user_upload/` path whose shape the bucket cannot hold. Only traversal
    (`..`) and NUL are rejected outright; those and unreadable images end in the default background, never a 500.
    """
    store = get_user_upload_store()
    if store.enabled:
        try:
            key = profile_bg_object_key(img_path)
        except ValueError as exc:
            logger.warning("profile.bg_rejected path=%r reason=%s", img_path, exc)
            return None
        if key is None and "user_upload" in img_path:
            logger.info("profile.bg_not_a_bucket_key path=%r (local read)", img_path)
        if key is not None:
            data = await store.fetch(key)
            if data is not None:
                try:
                    return await run_in_pool(get_encoded_image_ref, data)
                except (OSError, ValueError) as exc:
                    logger.warning("profile.bg_undecodable key=%s exc=%s: %s", key, type(exc).__name__, exc)
                    return None
            if not store.local_fallback:
                return None
    try:
        return await get_asset_image_ref(ASSETS_BASE_DIR, img_path, on_missing="raise")
    except (FileNotFoundError, OSError, ValueError):
        return None


async def _build_profile_canvas(rqd: ProfileRequest) -> Canvas:
    """Build the profile widget tree (shared by the Pillow and Skia render paths)."""
    # 玩家基本信息
    profile = rqd.profile
    # 个人信息卡组
    pcards = rqd.pcards
    # 头像
    avatar_img = await get_asset_image_ref(ASSETS_BASE_DIR, profile.leader_image_path)
    # 背景设置
    # 使用传入的背景图片，如果没有则使用默认蓝色背景
    bg_settings = rqd.bg_settings if rqd.bg_settings is not None else ProfileBgSettings()
    bg_img = await _load_profile_background(bg_settings.img_path) if bg_settings.img_path else None
    bg = ImageBg(bg_img, blur=False, fade=0) if bg_img is not None else SEKAI_BLUE_BG
    ui_bg = roundrect_bg(
        fill=(255, 255, 255, bg_settings.alpha), blur_glass=True, blur_glass_kwargs={"blur": bg_settings.blur}
    )
    # 称号
    honors = rqd.honors
    # 歌曲完成情况 / 角色等级
    diff_count = _build_profile_diff_count(rqd.music_difficulty_count)
    character_rank = _build_profile_character_rank_lookup(rqd.character_rank)

    # 挑战live等级
    solo_live = rqd.solo_live
    # 多人live统计
    multi_live = rqd.multi_live

    vertical = bg_settings.vertical
    layout_ctx = _ProfileLayoutContext(
        request=rqd,
        profile=profile,
        avatar_img=avatar_img,
        ui_bg=ui_bg,
        pcards=pcards,
        honors=honors,
        diff_count=diff_count,
        character_rank=character_rank,
        solo_live=solo_live,
        multi_live=multi_live,
    )
    modules = await _build_profile_layout_modules(layout_ctx)

    canvas = Canvas(bg=bg).set_padding(BG_PADDING)
    if not vertical:
        root = HSplit().set_content_align("lt").set_item_align("lt").set_sep(16)
        right_column = VSplit().set_content_align("c").set_item_align("c").set_sep(16)
        right_column.add_item(modules["play"])
        right_column.add_item(modules["growth"])
        root.add_item(modules["info"])
        root.add_item(right_column)
        canvas.add_item(root)
    else:
        root = VSplit().set_content_align("c").set_item_align("c").set_sep(16).set_item_bg(ui_bg)
        for module in modules.values():
            module.set_bg(None)
            root.add_item(module)
        canvas.add_item(root)
        # The shared item bg spans the column; widen the info panel to it so its frame does too.
        # Sizes are cached on first measure, so the panel's own width is read from its content.
        if rqd.profile.has_frame:
            info = modules["info"]
            natural = info._get_content_size()[0] + 2 * info.h_padding
            column = max(module._get_self_size()[0] for key, module in modules.items() if key != "info")
            info.set_w(max(natural, column))
    # after placement (and the vertical layout's bg swap) so the wrapper takes the panel's slot
    await _frame_profile_info_panel(layout_ctx, modules["info"])

    add_request_watermark(
        canvas,
        rqd,
        extra_suffix="This background is user-uploaded." if bg_settings.img_path else None,
    )
    return canvas


_PROFILE_SCALE = 1.5
_PROFILE_ENDPOINT = "profile"


async def compose_profile_image(rqd: ProfileRequest) -> Image.Image:
    """合成个人信息图片 (Pillow 路径)。"""
    return await (await _build_profile_canvas(rqd)).get_img(_PROFILE_SCALE)


async def try_render_profile_payload(rqd: ProfileRequest) -> EncodedImagePayload | None:
    """Skia 路径：经 IRPainter 渲染同一棵 widget 树；不可用时返回 None 回退 Pillow。

    没有整页 payload 缓存,这是有意的:调用方 (cloud) 已按 payload 去重——命中就不会调到 drawing——
    所以同一个 payload 不会来第二次,这里再加一层页面缓存永远不可能命中,而每次 miss 仍会 insert,
    把真正会命中的条目挤出共享 LRU。跨请求的复用发生在更下层:Rust 的 Moka 栅格缓存和 Pillow 的
    全局 resize 缓存按素材路径/尺寸缓存单个图层,那是跨用户共享的。"""
    if not skia_plot_enabled():
        return None
    canvas = await _build_profile_canvas(rqd)
    return await render_canvas_payload(canvas, endpoint=_PROFILE_ENDPOINT, scale=_PROFILE_SCALE)


def _profile_card_data_source_label(name: str | None) -> str:
    if not name:
        return "数据"
    if name.endswith("数据"):
        return name[:-2]
    return name


def _profile_card_level_label(name: str | list[Widget], mysekai_level: int | None) -> str | None:
    """``MySekai Lv.N``, or the compact ``MSLv.N`` next to a long name (its visible text, or its text items)."""
    if not mysekai_level:
        return None
    if isinstance(name, str):
        name_length = get_str_display_length(name)
    else:
        name_length = sum(get_str_display_length(item.text) for item in name if isinstance(item, TextBox))
    return f"MySekai Lv.{mysekai_level}" if name_length <= 12 else f"MSLv.{mysekai_level}"


def _profile_card_summary_line(profile: BasicProfile, data_sources: list[ProfileDataSource]) -> str:
    user_id = process_hide_uid(profile.is_hide_uid, profile.id, keep=6)
    summary_line = f"{profile.region.upper()}: {user_id}"
    primary_source = data_sources[0] if data_sources else None
    if len(data_sources) <= 1 and primary_source and primary_source.name:
        summary_line += f" {primary_source.name}"
    return summary_line


def _profile_card_update_lines(data_sources: list[ProfileDataSource], timezone_name: str | None) -> list[str]:
    if len(data_sources) <= 1:
        primary_source = data_sources[0] if data_sources else None
        if primary_source is None or not primary_source.update_time:
            return []
        update_time = datetime_from_millis(primary_source.update_time, timezone_name)
        return [f"更新时间: {format_info_panel_update_time(update_time, timezone_name)}"]

    update_lines = []
    for data_source in data_sources[:2]:
        if not data_source.update_time:
            continue
        update_time = datetime_from_millis(data_source.update_time, timezone_name)
        update_time_text = format_info_panel_update_time(update_time, timezone_name)
        update_lines.append(f"{_profile_card_data_source_label(data_source.name)}更新时间: {update_time_text}")
    return update_lines


# ---------------------------------------------------------------------------
# Profile card look ("信息条式"): a fixed-width card, the avatar in a white well ringed in the region
# colour with a region badge, the name with the game rank and MySekai level chips, a masked UID line
# (timezone once), one soft well per data source (age dot · source · time · relative age) and, when the
# caller sent an error message, a notice strip across the card. Same information as the old card.
# ---------------------------------------------------------------------------

_CARD_INK = (40, 44, 64, 255)
_CARD_INK_SOFT = (70, 74, 92, 255)
_CARD_DIM = (120, 124, 138, 255)
_CARD_WELL = (255, 255, 255, 200)
_CARD_WELL_SOFT = (255, 255, 255, 120)
_CARD_CHIP_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=13, color=WHITE)
_CARD_BADGE_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=12, color=WHITE)
_CARD_NAME_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=_CARD_INK)
_CARD_LINE_STYLE = TextStyle(font=DEFAULT_FONT, size=14, color=_CARD_INK_SOFT)
_CARD_ID_STYLE = TextStyle(font=DEFAULT_FONT, size=14, color=_CARD_DIM)
_CARD_SOURCE_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=13, color=_CARD_INK_SOFT)
_CARD_AGE_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=13, color=_CARD_DIM)
_CARD_NOTICE_STYLE = TextStyle(font=DEFAULT_FONT, size=14, color=(176, 84, 24, 255))
_CARD_NOTICE_FILL = (255, 234, 216, 235)
_CARD_NOTICE_INK = (176, 84, 24, 255)
_CARD_RANK_CHIP = (58, 140, 220, 255)
_CARD_LEVEL_CHIP = (51, 190, 178, 255)
_CARD_REGION_CHIPS: dict[str, tuple[int, int, int, int]] = {
    "JP": (226, 76, 96, 255),
    "CN": (232, 130, 40, 255),
    "EN": (58, 140, 220, 255),
    "TW": (52, 168, 96, 255),
    "KR": (150, 72, 210, 255),
}
_CARD_REGION_CHIP_FALLBACK = (120, 126, 140, 255)
# relative age colour: fresh (< 1 day) stays neutral, then amber, then red
_CARD_AGE_WARN_DAYS = 1
_CARD_AGE_STALE_DAYS = 7
_CARD_AGE_FRESH = (140, 144, 156, 255)
_CARD_AGE_WARN = (214, 140, 40, 255)
_CARD_AGE_STALE = (214, 84, 96, 255)
_CARD_W = 470
_CARD_PAD_X, _CARD_PAD_Y = 16, 12
_CARD_INNER_W = _CARD_W - 2 * _CARD_PAD_X
_CARD_AVATAR = 80
_CARD_AVATAR_WELL = _CARD_AVATAR + 12  # the avatar fills the well edge to edge inside the 2 px region ring
_CARD_AVATAR_RING = 2
_CARD_TEXT_W = _CARD_INNER_W - _CARD_AVATAR_WELL - 14
_CARD_ERROR_W = 300  # legacy width of the standalone error module
# what a data-source row leaves for the relative age: padding, dot, name 106, time 112 and three 8 px gaps
_CARD_SOURCE_AGE_W = _CARD_TEXT_W - 2 * 8 - 8 - 106 - 112 - 3 * 8


def _profile_card_chip(text: str, fill: tuple[int, int, int, int], *, style: TextStyle = _CARD_CHIP_STYLE) -> TextBox:
    offset_y = ink_centered_text_offset_y(style.font, style.size, text, style.size)
    return (
        TextBox(text, style)
        .set_padding((8, 3) if style is _CARD_CHIP_STYLE else (6, 2))
        .set_text_offset((0, offset_y))
        .set_bg(RoundRectBg(fill, 9 if style is _CARD_CHIP_STYLE else 8, blur_glass=False))
    )


def _profile_card_region_chip_fill(region: str) -> tuple[int, int, int, int]:
    return _CARD_REGION_CHIPS.get(region.strip().upper(), _CARD_REGION_CHIP_FALLBACK)


def _profile_card_uid_line(profile: BasicProfile) -> str:
    return f"ID {process_hide_uid(profile.is_hide_uid, profile.id, keep=6)}"


def _profile_card_rank_label(rank: int | None) -> str | None:
    """The game account rank (``userGamedata.rank``) as ``Lv.N``; None when the caller sent none."""
    return f"Lv.{rank}" if rank else None


def _profile_card_age_text(update_time, now) -> tuple[str, tuple[int, int, int, int]]:
    """Relative age of ``update_time`` against ``now`` (both aware datetimes) and its hint colour.

    Hour granularity under a day and day granularity beyond it, so a cached image only drifts at
    those boundaries; the colour warns after :data:`_CARD_AGE_WARN_DAYS` / :data:`_CARD_AGE_STALE_DAYS`.
    """
    seconds = max(0.0, (now - update_time).total_seconds())
    days = int(seconds // 86400)
    if days >= _CARD_AGE_STALE_DAYS:
        return f"{days} 天前", _CARD_AGE_STALE
    if days >= _CARD_AGE_WARN_DAYS:
        return ("昨天" if days == 1 else f"{days} 天前"), _CARD_AGE_WARN
    hours = int(seconds // 3600)
    return (f"{hours} 小时前" if hours >= 1 else "1 小时内"), _CARD_AGE_FRESH


def _profile_card_source_rows(
    data_sources: list[ProfileDataSource], timezone_name: str | None, now
) -> list[tuple[str, str, str, tuple[int, int, int, int]]]:
    """``(source name, local time, relative age, age colour)`` per timestamped source.

    The first source alone, or the first two when several were sent — exactly the sources the old card
    listed. The timezone is not repeated per row; the card prints it once on the UID line.
    """
    rows = []
    sources = data_sources[:1] if len(data_sources) <= 1 else data_sources[:2]
    for data_source in sources:
        if not data_source.update_time:
            continue
        update_time = datetime_from_millis(data_source.update_time, timezone_name)
        age, color = _profile_card_age_text(update_time, now)
        rows.append((data_source.name or "数据", update_time.strftime("%m-%d %H:%M:%S"), age, color))
    return rows


async def _build_profile_card_avatar_module(rqd: ProfileCardRequest) -> Widget | None:
    if not rqd.profile:
        return None
    avatar_img = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.profile.leader_image_path)
    region = rqd.profile.region.upper()
    ring = _profile_card_region_chip_fill(region)
    well = _CARD_AVATAR_WELL
    inner = well - 2 * _CARD_AVATAR_RING
    # the avatar fills the well up to the region-coloured ring; the region chip sits on the ID line
    with Frame().set_size((well, well)).set_content_align("c").set_bg(RoundRectBg(ring, 18, blur_glass=False)) as ret:
        with RoundClipFrame(18 - _CARD_AVATAR_RING).set_size((inner, inner)).set_content_align("c"):
            ImageBox(avatar_img, size=(inner, inner), use_alpha_blend=False).set_content_align("c")
    return ret


def _profile_card_visible_name(nickname: str) -> str:
    """The characters of the (truncated) nickname that are drawn, colour tags removed."""
    return "".join(segment["text"] for segment in parse_colored_text_segments(truncate(nickname, 64)))


def _profile_card_name(nickname: str, free: int) -> list[Widget]:
    """The name, kept inside the ``free`` px the rank / level chips leave in the text column.

    A plain name shrinks to the free width. A colour-tagged name keeps its per-segment colours
    (``colored_text_box`` cannot shrink) while it fits; a long one falls back to the plain, shrinking
    ink-coloured text of its visible characters. Returns the widgets for the name row; the caller sets
    the row's items, so a measured-and-rejected colour box never stays in the row.
    """
    text = truncate(nickname, 64)
    segments = parse_colored_text_segments(text)
    if len(segments) > 1 or segments[0]["color"] is not None:
        colored = colored_text_box(text, _CARD_NAME_STYLE, padding=0)
        if colored._get_self_size()[0] <= free:
            return [colored]
        text = "".join(segment["text"] for segment in segments)
    length = get_str_display_length(text)
    size = 24 if length <= 10 else (20 if length <= 16 else 18)
    return [TextBox(text, _CARD_NAME_STYLE.replace(size=size), overflow="shrink").set_w(free)]


def _fit_text_box_width(box: TextBox, max_w: int) -> None:
    """Shrink a one-line ``TextBox`` so its outer width (padding included) is at most ``max_w``."""
    if box._get_self_size()[0] <= max_w:
        return
    box.set_w(max_w)  # the outer width, padding included
    box._calc_w = box._calc_h = None  # _get_self_size caches the natural size measured above


def _build_profile_card_identity_module(rqd: ProfileCardRequest, data_sources: list) -> Widget | None:
    profile = rqd.profile
    if not profile:
        return None
    now = datetime_from_millis(rqd.dt, rqd.timezone) if rqd.dt else request_now(rqd.timezone)

    with VSplit().set_content_align("lt").set_item_align("lt").set_sep(4) as identity:
        with HSplit().set_content_align("l").set_item_align("c").set_sep(8) as name_row:
            chips = []
            if rank_text := _profile_card_rank_label(rqd.rank):
                chips.append(_profile_card_chip(rank_text, _CARD_RANK_CHIP))
            visible_name = _profile_card_visible_name(profile.nickname)
            if ms_lv_text := _profile_card_level_label(visible_name, rqd.mysekai_level):
                chips.append(_profile_card_chip(ms_lv_text, _CARD_LEVEL_CHIP))
            # The chips keep their measured width (it grows with the font's Latin/digit advances and the
            # number of digits); the name gets whatever is left of the text column.
            free = _CARD_TEXT_W - sum(chip._get_self_size()[0] + 8 for chip in chips)
            name_row.set_items([*_profile_card_name(profile.nickname, free), *chips])
        with HSplit().set_content_align("l").set_item_align("c").set_sep(8) as id_row:
            region = profile.region.upper()
            chip = _profile_card_chip(region, _profile_card_region_chip_fill(region), style=_CARD_BADGE_STYLE)
            # The row must never outgrow the text column (an unmasked 19-digit ID plus a long timezone
            # does): the timezone moves to its own line when it does not fit whole, the ID shrinks last.
            free = _CARD_TEXT_W - chip._get_self_size()[0] - 8
            uid = TextBox(_profile_card_uid_line(profile), _CARD_ID_STYLE, overflow="shrink")
            _fit_text_box_width(uid, free)
            room = free - uid._get_self_size()[0] - 8
            tz_style = _CARD_ID_STYLE.replace(size=12)
            wrap_tz = False
            if rqd.timezone:
                tz = TextBox(f"· {rqd.timezone}", tz_style, overflow="shrink")
                if tz._get_self_size()[0] > room:
                    id_row.set_items([chip, uid])
                    wrap_tz = True
        if wrap_tz:
            _fit_text_box_width(TextBox(rqd.timezone, tz_style, overflow="shrink"), _CARD_TEXT_W)
        for name, local_time, age, color in _profile_card_source_rows(data_sources, rqd.timezone, now):
            with (
                HSplit()
                .set_w(_CARD_TEXT_W)
                .set_content_align("l")
                .set_item_align("c")
                .set_sep(8)
                .set_padding((8, 3))
                .set_bg(RoundRectBg(_CARD_WELL_SOFT, 8, blur_glass=False))
            ):
                Spacer(w=8, h=8).set_bg(RoundRectBg(color, 4, blur_glass=False))
                # dot 8 + name 106 + time 112 + age; the age shrinks rather than outgrow the text column
                TextBox(name, _CARD_SOURCE_STYLE, overflow="shrink").set_w(106)
                TextBox(local_time, _CARD_LINE_STYLE).set_w(112)
                age_box = TextBox(age, _CARD_AGE_STYLE.replace(color=color), overflow="shrink")
                _fit_text_box_width(age_box, _CARD_SOURCE_AGE_W)

    return identity


def _build_profile_card_error_module(rqd: ProfileCardRequest) -> Widget | None:
    if not rqd.error_message:
        return None
    width = _CARD_INNER_W if rqd.profile else _CARD_ERROR_W
    with (
        HSplit()
        .set_w(width)
        .set_content_align("l")
        .set_item_align("t")
        .set_sep(8)
        .set_padding((10, 6))
        .set_bg(RoundRectBg(_CARD_NOTICE_FILL, 8, blur_glass=False))
    ) as notice:
        TextBox("!", TextStyle(font=DEFAULT_HEAVY_FONT, size=13, color=WHITE)).set_padding((7, 1)).set_bg(
            RoundRectBg(_CARD_NOTICE_INK, 8, blur_glass=False)
        )
        TextBox(rqd.error_message, _CARD_NOTICE_STYLE, line_count=3, use_real_line_count=True).set_w(width - 64)
    return notice


async def _build_profile_card_modules(rqd: ProfileCardRequest) -> list[Widget]:
    data_sources = [item for item in rqd.data_sources if item]
    avatar_module = await _build_profile_card_avatar_module(rqd)
    identity_module = _build_profile_card_identity_module(rqd, data_sources)
    error_module = _build_profile_card_error_module(rqd)

    modules: list[Widget] = []
    if avatar_module is not None:
        modules.append(avatar_module)
    if identity_module is not None:
        modules.append(identity_module)
    if error_module is not None:
        modules.append(error_module)
    return modules


# 获取玩家个人信息的简单卡片控件
async def get_profile_card(rqd: ProfileCardRequest, *, blur_glass: bool = True) -> Frame:
    r"""get_profile_card

    获取玩家个人信息的简单卡片控件

    Args
    ----
        rqd : ProfileCardRequest
        blur_glass : bool
            毛玻璃背景（含阴影）；独立信息面板没有可模糊的底图，传 False 只画纯色圆角矩形

    Returns
    -------
    Frame
    """
    bg_alpha = rqd.bg_alpha if rqd.bg_alpha is not None else 150

    # Widgets auto-attach to the current active container on construction; the modules are built
    # detached and placed explicitly: avatar | identity on one row, the notice strip below.
    with (
        Frame().set_bg(roundrect_bg(alpha=bg_alpha, blur_glass=blur_glass)).set_padding((_CARD_PAD_X, _CARD_PAD_Y)) as f
    ):
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8) as column:
            if rqd.profile:
                column.set_w(_CARD_INNER_W)
            with HSplit().set_content_align("lt").set_item_align("t").set_sep(14) as row:
                modules = await _build_profile_card_modules(rqd)
            row.set_items([m for m in modules if m is not modules[-1] or not rqd.error_message])
            if rqd.error_message and modules:
                column.add_item(modules[-1])
    if rqd.profile and rqd.profile.has_frame and rqd.profile.frame_paths:
        # The card is the player's list row: frame it the way the friend list / ranking rows are.
        return await wrap_with_player_frame(f, rqd.profile.frame_paths, cell="horizontal")
    return f


# ---------------------------------------------------------------------------
# Standalone info panel (/信息面板): the profile card on its own, always PNG. Nothing but the card, its
# frame and a two-line watermark (the request DT, then the credit) is drawn: no page background, and the
# card is a plain rounded rectangle without the glass blur or its shadow, so every other pixel is clear.
# ---------------------------------------------------------------------------

_INFO_PANEL_ENDPOINT = "profile_info_panel"
_INFO_PANEL_SCALE = 2.0
# room for the frame's ornaments, which overhang the card by up to 16 UI units at card scale
_INFO_PANEL_PADDING = 16
# Opaque enough to read over any chat background; the embedded card's 150 assumes a page behind it.
_INFO_PANEL_BG_ALPHA = 235


async def _build_info_panel_canvas(rqd: ProfileCardRequest) -> Canvas:
    card = rqd if rqd.bg_alpha is not None else rqd.model_copy(update={"bg_alpha": _INFO_PANEL_BG_ALPHA})
    with Canvas(bg=None).set_padding(_INFO_PANEL_PADDING) as canvas:
        await get_profile_card(card, blur_glass=False)
    add_watermark(canvas, build_request_dt_watermark_text(rqd))
    return canvas


async def compose_info_panel_image(rqd: ProfileCardRequest) -> Image.Image:
    """Pillow reference for the standalone info panel."""
    return await (await _build_info_panel_canvas(rqd)).get_img(_INFO_PANEL_SCALE)


async def try_render_info_panel_payload(rqd: ProfileCardRequest) -> EncodedImagePayload | None:
    """Skia path; PNG regardless of the global export format, because the background is transparent."""
    if not skia_plot_enabled():
        return None
    canvas = await _build_info_panel_canvas(rqd)
    return await render_canvas_payload(
        canvas, endpoint=_INFO_PANEL_ENDPOINT, scale=_INFO_PANEL_SCALE, export_format="png"
    )
