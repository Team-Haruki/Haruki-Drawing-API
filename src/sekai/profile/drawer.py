import asyncio
from dataclasses import dataclass
import logging
import time

from PIL import Image

from src.core.heavy_render_pool import EncodedImagePayload
from src.sekai.base.draw import (
    BG_PADDING,
    DIFF_COLORS,
    PLAY_RESULT_COLORS,
    SEKAI_BLUE_BG,
    add_request_watermark,
    roundrect_bg,
)
from src.sekai.base.painter import (
    ADAPTIVE_SHADOW,
    ADAPTIVE_WB,
    BLACK,
    DEFAULT_BOLD_FONT,
    DEFAULT_FONT,
    RED,
    WHITE,
    Painter,
    get_font,
    get_font_desc,
    get_text_size,
)
from src.sekai.base.plot import (
    Canvas,
    ColoredTextBox,
    Frame,
    Grid,
    HSplit,
    ImageBg,
    ImageBox,
    RoundRectBg,
    Spacer,
    TextBox,
    TextStyle,
    VSplit,
    Widget,
    colored_text_box,
)
from src.sekai.base.timezone import datetime_from_millis
from src.sekai.base.utils import (
    AssetImageRef,
    ImageSource,
    build_rendered_image_cache_key,
    get_asset_image_ref,
    get_composed_image_cached,
    get_composed_image_disk_cached,
    get_img_resized,
    get_str_display_length,
    put_composed_image_cache,
    put_composed_image_disk_cache,
    truncate,
)
from src.sekai.honor.drawer import HonorRequest, compose_full_honor_image
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


def _ascender_top_to_painter_y(font_path: str, font_size: int, ascender_top_y: int) -> int:
    """Convert an ``ImageDraw.text`` y (its default ``"la"`` anchor = top of the ascender)
    into the y ``Painter.text`` expects (it anchors the baseline at ``y + ink-height("哇")``).

    The two differ by ``ascent - ink_height("哇")`` — 4px for the bold font at size 20 — so a
    layout constant lifted straight from the old ImageDraw code lands the text that much too
    high. The gap is font- and size-dependent, so derive it from the metrics rather than
    folding a fudge factor into the constant."""
    font = get_font(font_path, font_size)
    return ascender_top_y + font.getmetrics()[0] - get_text_size(font, "哇")[1]


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
    tasks = [
        get_asset_image_ref(ASSETS_BASE_DIR, rqd.card_thumbnail_path),
        get_asset_image_ref(ASSETS_BASE_DIR, rare_img_path),
    ]
    if rqd.frame_img_path:
        keys.append("frame")
        tasks.append(get_asset_image_ref(ASSETS_BASE_DIR, rqd.frame_img_path))
    if rqd.is_pcard and rqd.train_rank and rqd.train_rank_img_path:
        keys.append("rank")
        tasks.append(get_asset_image_ref(ASSETS_BASE_DIR, rqd.train_rank_img_path))
    if rqd.attr_img_path:
        keys.append("attr")
        tasks.append(get_asset_image_ref(ASSETS_BASE_DIR, rqd.attr_img_path))
    loaded = dict(zip(keys, await asyncio.gather(*tasks)))
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
    ) -> None:
        super().__init__(layers.base, image_size_mode=image_size_mode, size=size)
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
        p.paste(self.image, (0, 0), (w, h))
        pcard = rqd.is_pcard
        if pcard:
            bar_h = round(24 * sy)
            p.rect((0, h - bar_h), (w, bar_h), fill=(70, 70, 100, 255))
            text = rqd.custom_text or f"Lv.{rqd.level}"
            font_size = max(1, round(20 * sy))
            font = get_font_desc(DEFAULT_BOLD_FONT, font_size)
            y = _ascender_top_to_painter_y(DEFAULT_BOLD_FONT, font_size, h - round(31 * sy))
            p.text(text, (round(6 * sx), y), font=font, fill=WHITE)
        # The overlays go through paste_with_alpha_blend, not paste: Pillow's paste(im, pos, im)
        # lerps the DESTINATION alpha toward the layer's, so an anti-aliased frame/star edge
        # would leave the composed thumbnail translucent there and the page background (and the
        # drop shadow underneath) would bleed through as a halo. The legacy composer got away
        # with plain pastes because it finished with img.putalpha(mask), hard-resetting alpha to
        # opaque; the clip only multiplies alpha, so it cannot undo that. alpha_composite keeps
        # dst alpha at 255 and is what the Skia backend already does for both paste variants.
        if layers.frame is not None:
            p.paste_with_alpha_blend(layers.frame, (0, 0), (w, h))
        if pcard and rqd.train_rank and layers.rank is not None:
            rank_w, rank_h = max(1, round(w * 0.35)), max(1, round(h * 0.35))
            p.paste_with_alpha_blend(layers.rank, (w - rank_w, h - rank_h), (rank_w, rank_h))
        if layers.attr is not None:
            p.paste_with_alpha_blend(layers.attr, (round(sx), 0), (max(1, round(w * 0.22)), max(1, round(h * 0.25))))
        rare_scale = 0.17 if not pcard else 0.15
        rare_w, rare_h = max(1, round(w * rare_scale)), max(1, round(h * rare_scale))
        hoffset, voffset = round(6 * sx), round((24 if pcard else 6) * sy)
        for i in range(rare_count(rqd.rare)):
            p.paste_with_alpha_blend(layers.rare, (hoffset + rare_w * i, h - rare_h - voffset), (rare_w, rare_h))
        p.pop_clip()


@dataclass(slots=True)
class PlayerFrameLayers:
    """头像框六部件的 header-only 图源引用（缺文件时为占位 PIL 图）。"""

    base: AssetImageRef | Image.Image
    centertop: AssetImageRef | Image.Image
    leftbottom: AssetImageRef | Image.Image
    lefttop: AssetImageRef | Image.Image
    rightbottom: AssetImageRef | Image.Image
    righttop: AssetImageRef | Image.Image


async def get_player_frame_layers(frame_paths) -> PlayerFrameLayers:
    r"""获取头像框六部件的图源引用（不解码像素）。

    Args
    ----
    frame_paths : PlayerFramePaths
        头像框各部件路径
    """
    base, ct, lb, lt, rb, rt = await asyncio.gather(
        get_asset_image_ref(ASSETS_BASE_DIR, frame_paths.base),
        get_asset_image_ref(ASSETS_BASE_DIR, frame_paths.centertop),
        get_asset_image_ref(ASSETS_BASE_DIR, frame_paths.leftbottom),
        get_asset_image_ref(ASSETS_BASE_DIR, frame_paths.lefttop),
        get_asset_image_ref(ASSETS_BASE_DIR, frame_paths.rightbottom),
        get_asset_image_ref(ASSETS_BASE_DIR, frame_paths.righttop),
    )
    return PlayerFrameLayers(base=base, centertop=ct, leftbottom=lb, lefttop=lt, rightbottom=rb, righttop=rt)


class PlayerFrameBox(Widget):
    """玩家头像框，两个后端经 Painter 原语原生绘制（旧 700×700 Pillow 预合成的子树化）。

    复刻旧 ``get_player_frame_image`` 的几何：700×700 逻辑画布上，base 按源角 20px 做
    9-slice 放大到 50px 铺满 border=100 内的 500×500 内框，角落/顶部装饰件按 1.5× 贴在
    border2=80 处，整体按 ``frame_w/500`` 缩放。这里直接在最终尺寸下算整数几何——每个
    部件只重采样一次（替代旧的先合成再整图缩放），base 切片经 ``src_rect`` 直达两后端，
    Skia 路径零 Python 解码、切片栅格进 Rust Moka 缓存跨请求复用。
    """

    _SRC_CORNER = 20  # base 素材上的 9-slice 源角宽（像素，旧实现的 corner）

    def __init__(self, layers: PlayerFrameLayers, frame_w: int) -> None:
        super().__init__()
        self.layers = layers
        self._fscale = frame_w / 500  # 旧内框 inner_w=500 → 最终 frame_w
        self.prefetch_image_sources = [
            layers.base,
            layers.centertop,
            layers.leftbottom,
            layers.lefttop,
            layers.rightbottom,
            layers.righttop,
        ]

    def _get_content_size(self) -> tuple[int, int]:
        outer = max(1, round(700 * self._fscale))
        return (outer, outer)

    def _draw_content(self, p: Painter) -> None:
        s = self._fscale
        layers = self.layers
        outer, _ = self._get_content_size()
        border = round(100 * s)
        inner = round(500 * s)
        c2 = max(1, round(50 * s))
        edge = max(1, inner - 2 * c2)
        border2 = round(80 * s)

        base = layers.base
        bw, bh = base.size
        c = max(1, min(self._SRC_CORNER, bw // 2, bh // 2))
        far = border + inner - c2
        # base 9-slice：四角 + 四边（拉伸）
        p.paste_with_alpha_blend(base, (border, border), (c2, c2), src_rect=(0, 0, c, c))
        p.paste_with_alpha_blend(base, (far, border), (c2, c2), src_rect=(bw - c, 0, bw, c))
        p.paste_with_alpha_blend(base, (border, far), (c2, c2), src_rect=(0, bh - c, c, bh))
        p.paste_with_alpha_blend(base, (far, far), (c2, c2), src_rect=(bw - c, bh - c, bw, bh))
        p.paste_with_alpha_blend(base, (border, border + c2), (c2, edge), src_rect=(0, c, c, bh - c))
        p.paste_with_alpha_blend(base, (far, border + c2), (c2, edge), src_rect=(bw - c, c, bw, bh - c))
        p.paste_with_alpha_blend(base, (border + c2, border), (edge, c2), src_rect=(c, 0, bw - c, c))
        p.paste_with_alpha_blend(base, (border + c2, far), (edge, c2), src_rect=(c, bh - c, bw - c, bh))

        # 装饰件（旧实现先 1.5× 再整图 ×s，这里一步到位）
        def dec_size(part) -> tuple[int, int]:
            return (max(1, round(part.width * 1.5 * s)), max(1, round(part.height * 1.5 * s)))

        lb_w, lb_h = dec_size(layers.leftbottom)
        p.paste_with_alpha_blend(layers.leftbottom, (border2, outer - border2 - lb_h), (lb_w, lb_h))
        rb_w, rb_h = dec_size(layers.rightbottom)
        p.paste_with_alpha_blend(layers.rightbottom, (outer - border2 - rb_w, outer - border2 - rb_h), (rb_w, rb_h))
        lt_w, lt_h = dec_size(layers.lefttop)
        p.paste_with_alpha_blend(layers.lefttop, (border2, border2), (lt_w, lt_h))
        rt_w, rt_h = dec_size(layers.righttop)
        p.paste_with_alpha_blend(layers.righttop, (outer - border2 - rt_w, border2), (rt_w, rt_h))
        ct_w, ct_h = dec_size(layers.centertop)
        p.paste_with_alpha_blend(layers.centertop, ((outer - ct_w) // 2, border2 - ct_h // 2), (ct_w, ct_h))


# 获取带框头像控件
async def get_avatar_widget_with_frame(
    is_frame: bool, frame_paths, avatar_img: ImageSource, avatar_w: int, frame_data: list[dict]
) -> Frame:
    frame_layers = None
    if is_frame and frame_paths:
        frame_layers = await get_player_frame_layers(frame_paths)

    with Frame().set_size((avatar_w, avatar_w)).set_content_align("c").set_allow_draw_outside(True) as ret:
        ImageBox(avatar_img, size=(avatar_w, avatar_w), use_alpha_blend=False)
        if frame_layers is not None:
            PlayerFrameBox(frame_layers, avatar_w + 5)
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
    return await get_avatar_widget_with_frame(
        is_frame=bool(ctx.request.profile.has_frame),
        frame_paths=ctx.request.frame_paths,
        avatar_img=ctx.avatar_img,
        avatar_w=128,
        frame_data=[],
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
    x_icon = await get_img_resized(ASSETS_BASE_DIR, ctx.request.x_icon_path, 24, 24)
    root.add_item(ImageBox(x_icon.convert("RGBA"), image_size_mode="original").set_offset((16, 0)))
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
    honor_imgs = await asyncio.gather(
        *[compose_full_honor_image(honor) for honor in ctx.honors],
        return_exceptions=True,
    )
    for img in honor_imgs:
        if isinstance(img, Exception):
            logger.warning("skip broken honor asset in profile image: %s", img)
            continue
        if img:
            root.add_item(ImageBox(img, size=(None, 48), shadow=True))
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
    if bg_settings.img_path:
        try:
            bg_img = await get_asset_image_ref(ASSETS_BASE_DIR, bg_settings.img_path, on_missing="raise")
            bg = ImageBg(bg_img, blur=False, fade=0)
        except (FileNotFoundError, OSError, ValueError):
            bg = SEKAI_BLUE_BG
    else:
        bg = SEKAI_BLUE_BG
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


async def _build_profile_card_avatar_module(rqd: ProfileCardRequest) -> Widget | None:
    if not rqd.profile:
        return None
    avatar_img = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.profile.leader_image_path)
    return await get_avatar_widget_with_frame(
        is_frame=bool(rqd.profile.has_frame),
        frame_paths=None,
        avatar_img=avatar_img,
        avatar_w=80,
        frame_data=[],
    )


def _build_profile_card_identity_module(rqd: ProfileCardRequest, data_sources: list) -> Widget | None:
    if not rqd.profile:
        return None

    primary_source = data_sources[0] if data_sources else None
    with VSplit().set_content_align("c").set_item_align("l").set_sep(5) as identity:
        with HSplit().set_content_align("lb").set_item_align("lb").set_sep(5):
            hs = colored_text_box(
                truncate(rqd.profile.nickname, 64),
                TextStyle(
                    font=DEFAULT_BOLD_FONT,
                    size=24,
                    color=BLACK,
                    use_shadow=True,
                    shadow_offset=2,
                    shadow_color=ADAPTIVE_SHADOW,
                ),
            )
            if rqd.mysekai_level:
                name_length = 0
                for item in hs.items:
                    if isinstance(item, TextBox):
                        name_length += get_str_display_length(item.text)
                ms_lv_text = f"MySekai Lv.{rqd.mysekai_level}" if name_length <= 12 else f"MSLv.{rqd.mysekai_level}"
                TextBox(ms_lv_text, TextStyle(font=DEFAULT_FONT, size=18, color=BLACK))

        user_id = process_hide_uid(rqd.profile.is_hide_uid, rqd.profile.id, keep=6)
        summary_line = f"{rqd.profile.region.upper()}: {user_id}"
        if len(data_sources) <= 1 and primary_source and primary_source.name:
            summary_line += f" {primary_source.name}"
        TextBox(summary_line, TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))

        if len(data_sources) <= 1:
            if primary_source and primary_source.update_time:
                update_time = datetime_from_millis(primary_source.update_time, rqd.timezone)
                update_time_text = format_info_panel_update_time(update_time, rqd.timezone)
                TextBox(f"更新时间: {update_time_text}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
        else:
            for data_source in data_sources[:2]:
                if not data_source.update_time:
                    continue
                update_time = datetime_from_millis(data_source.update_time, rqd.timezone)
                update_time_text = format_info_panel_update_time(update_time, rqd.timezone)
                TextBox(
                    f"{_profile_card_data_source_label(data_source.name)}更新时间: {update_time_text}",
                    TextStyle(font=DEFAULT_FONT, size=16, color=BLACK),
                )

    return identity


def _build_profile_card_error_module(rqd: ProfileCardRequest) -> Widget | None:
    if not rqd.error_message:
        return None
    return TextBox(rqd.error_message, TextStyle(font=DEFAULT_FONT, size=20, color=RED), line_count=3).set_w(300)


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
async def get_profile_card(rqd: ProfileCardRequest) -> Frame:
    r"""get_profile_card

    获取玩家个人信息的简单卡片控件

    Args
    ----
        rqd : ProfileCardRequest

    Returns
    -------
    Frame
    """
    bg_alpha = rqd.bg_alpha if rqd.bg_alpha is not None else 150

    # Widgets auto-attach to the current active container on construction.
    # Build the card within nested contexts so the modules attach exactly once
    # to the inner row instead of being added both implicitly and manually.
    with Frame().set_bg(roundrect_bg(alpha=bg_alpha)).set_padding(16) as f:
        with HSplit().set_content_align("c").set_item_align("c").set_sep(14):
            await _build_profile_card_modules(rqd)
    return f
