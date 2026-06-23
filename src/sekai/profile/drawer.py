import asyncio
from dataclasses import dataclass
import logging
import time

from PIL import Image, ImageDraw

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
    get_text_size,
    resize_keep_ratio,
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
    build_rendered_image_cache_key,
    get_composed_image_cached,
    get_composed_image_disk_cached,
    get_image_asset_signature,
    get_img_from_path,
    get_img_resized,
    get_str_display_length,
    put_composed_image_cache,
    put_composed_image_disk_cache,
    run_in_pool,
    truncate,
)
from src.sekai.honor.drawer import HonorRequest, compose_full_honor_image
from src.sekai.skia_renderer.canvas import render_canvas_payload, skia_plot_enabled
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
    avatar_img: Image.Image
    ui_bg: RoundRectBg
    pcards: list[CardFullThumbnailRequest]
    honors: list[HonorRequest]
    diff_count: dict[str, dict[str, int]]
    character_rank: dict[int, int]
    solo_live: SoloLiveRank | None
    multi_live: MultiLiveTopScoreCount | None


def _compose_card_full_thumbnail_sync(
    rqd: CardFullThumbnailRequest,
    img: Image.Image,
    rare_img: Image.Image,
    frame_img: Image.Image | None,
    rank_img: Image.Image | None,
    attr_img: Image.Image | None,
) -> Image.Image:
    # 兼容 "rarity_4", "4_star", "4" 等格式
    if rqd.rare == "rarity_birthday":
        rare_num = 1
    else:
        import re

        match = re.search(r"(\d+)", rqd.rare)
        if match:
            rare_num = int(match.group(1))
        else:
            rare_num = 0

    img_w, img_h = img.size
    custom_text = rqd.custom_text or None
    pcard = rqd.is_pcard
    if pcard:
        draw = ImageDraw.Draw(img)
        draw.rectangle((0, img_h - 24, img_w, img_h), fill=(70, 70, 100, 255))
        if custom_text is not None:
            draw.text((6, img_h - 31), custom_text, font=get_font(DEFAULT_BOLD_FONT, 20), fill=WHITE)
        else:
            draw.text((6, img_h - 31), f"Lv.{rqd.level}", font=get_font(DEFAULT_BOLD_FONT, 20), fill=WHITE)

    if frame_img is not None:
        img.paste(frame_img, (0, 0), frame_img)

    if pcard and rqd.train_rank and rank_img is not None:
        rank_img_w, rank_img_h = rank_img.size
        img.paste(rank_img, (img_w - rank_img_w, img_h - rank_img_h), rank_img)

    if attr_img is not None:
        img.paste(attr_img, (1, 0), attr_img)

    hoffset, voffset = 6, 6 if not pcard else 24
    rare_w, rare_h = rare_img.size
    for i in range(rare_num):
        img.paste(rare_img, (hoffset + rare_w * i, img_h - rare_h - voffset), rare_img)

    mask = Image.new("L", (img_w, img_h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, img_w, img_h), radius=10, fill=255)
    img.putalpha(mask)
    return img


def _build_card_full_thumbnail_cache_key(
    rqd: CardFullThumbnailRequest,
    *,
    rare_img_path: str,
) -> str:
    request_payload = {
        "card_id": rqd.card_id,
        "card_thumbnail_path": rqd.card_thumbnail_path,
        "rare": rqd.rare,
        "frame_img_path": rqd.frame_img_path,
        "attr_img_path": rqd.attr_img_path,
        "rare_img_path": rare_img_path,
        "train_rank": rqd.train_rank,
        "train_rank_img_path": rqd.train_rank_img_path,
        "level": rqd.level,
        "custom_text": rqd.custom_text,
        "is_pcard": rqd.is_pcard,
    }
    asset_signatures = {
        "card_thumbnail": get_image_asset_signature(ASSETS_BASE_DIR, rqd.card_thumbnail_path),
        "rare": get_image_asset_signature(ASSETS_BASE_DIR, rare_img_path),
        "frame": get_image_asset_signature(ASSETS_BASE_DIR, rqd.frame_img_path),
        "attr": get_image_asset_signature(ASSETS_BASE_DIR, rqd.attr_img_path),
        "rank": get_image_asset_signature(ASSETS_BASE_DIR, rqd.train_rank_img_path),
    }
    return build_rendered_image_cache_key(
        "card_full_thumbnail",
        request_payload,
        asset_signatures=asset_signatures,
    )


async def get_card_full_thumbnail(rqd: CardFullThumbnailRequest) -> Image.Image:
    rare_img_path = rqd.birthday_icon_path if rqd.rare == "rarity_birthday" else rqd.rare_img_path
    cache_key = _build_card_full_thumbnail_cache_key(rqd, rare_img_path=rare_img_path)
    cached = get_composed_image_cached(cache_key)
    if cached is not None:
        return cached
    disk_cached = get_composed_image_disk_cached("card_full_thumbnail", cache_key)
    if disk_cached is not None:
        put_composed_image_cache(cache_key, disk_cached)
        return disk_cached

    _t0 = time.perf_counter()
    img = await get_img_from_path(ASSETS_BASE_DIR, rqd.card_thumbnail_path)
    img_w, img_h = img.size
    rare_scale = 0.17 if not rqd.is_pcard else 0.15
    tasks = {
        "rare_img": get_img_resized(
            ASSETS_BASE_DIR,
            rare_img_path,
            max(1, int(img_w * rare_scale)),
            max(1, int(img_h * rare_scale)),
        ),
    }
    if rqd.frame_img_path:
        tasks["frame_img"] = get_img_resized(ASSETS_BASE_DIR, rqd.frame_img_path, img_w, img_h)
    if rqd.is_pcard and rqd.train_rank and rqd.train_rank_img_path:
        tasks["rank_img"] = get_img_resized(
            ASSETS_BASE_DIR,
            rqd.train_rank_img_path,
            max(1, int(img_w * 0.35)),
            max(1, int(img_h * 0.35)),
        )
    if rqd.attr_img_path:
        tasks["attr_img"] = get_img_resized(
            ASSETS_BASE_DIR,
            rqd.attr_img_path,
            max(1, int(img_w * 0.22)),
            max(1, int(img_h * 0.25)),
        )

    keys = list(tasks.keys())
    _t1 = time.perf_counter()
    values = await asyncio.gather(*tasks.values())
    _t2 = time.perf_counter()
    loaded = dict(zip(keys, values))
    composed = await run_in_pool(
        _compose_card_full_thumbnail_sync,
        rqd,
        img,
        loaded["rare_img"],
        loaded.get("frame_img"),
        loaded.get("rank_img"),
        loaded.get("attr_img"),
    )
    _t3 = time.perf_counter()
    put_composed_image_cache(cache_key, composed)
    put_composed_image_disk_cache("card_full_thumbnail", cache_key, composed)
    if _t3 - _t0 >= 0.05:
        logger.info(
            "[perf] card_full_thumbnail miss: card=%s total=%.3fs load_base=%.3fs preload=%.3fs compose=%.3fs",
            rqd.card_id,
            _t3 - _t0,
            _t1 - _t0,
            _t2 - _t1,
            _t3 - _t2,
        )
    return composed


# 获取头像框图片，失败返回None
async def get_player_frame_image(frame_paths, frame_w: int) -> Image.Image | None:
    r"""get_player_frame_image

    获取头像框图片

    Args
    ----
    frame_paths : PlayerFramePaths
        头像框各部件路径
    frame_w : int
        头像框宽度
    """
    scale = 1.5
    corner = 20
    corner2 = 50
    w = 700
    border = 100
    border2 = 80
    inner_w = w - 2 * border

    _t0 = time.perf_counter()
    base, ct, lb, lt, rb, rt = await asyncio.gather(
        get_img_from_path(ASSETS_BASE_DIR, frame_paths.base),
        get_img_from_path(ASSETS_BASE_DIR, frame_paths.centertop),
        get_img_from_path(ASSETS_BASE_DIR, frame_paths.leftbottom),
        get_img_from_path(ASSETS_BASE_DIR, frame_paths.lefttop),
        get_img_from_path(ASSETS_BASE_DIR, frame_paths.rightbottom),
        get_img_from_path(ASSETS_BASE_DIR, frame_paths.righttop),
    )
    logger.debug("[perf] get_player_frame_image preload 6 parts: %.3fs", time.perf_counter() - _t0)

    ct = resize_keep_ratio(ct, scale, mode="scale")
    lt = resize_keep_ratio(lt, scale, mode="scale")
    lb = resize_keep_ratio(lb, scale, mode="scale")
    rt = resize_keep_ratio(rt, scale, mode="scale")
    rb = resize_keep_ratio(rb, scale, mode="scale")

    bw = base.width
    base_lt = base.crop((0, 0, corner, corner))
    base_rt = base.crop((bw - corner, 0, bw, corner))
    base_lb = base.crop((0, bw - corner, corner, bw))
    base_rb = base.crop((bw - corner, bw - corner, bw, bw))
    base_l = base.crop((0, corner, corner, bw - corner))
    base_r = base.crop((bw - corner, corner, bw, bw - corner))
    base_t = base.crop((corner, 0, bw - corner, corner))
    base_b = base.crop((corner, bw - corner, bw - corner, bw))

    p = Painter(size=(w, w))

    p.move_region((border, border), (inner_w, inner_w))
    p.paste(base_lt, (0, 0), (corner2, corner2))
    p.paste(base_rt, (inner_w - corner2, 0), (corner2, corner2))
    p.paste(base_lb, (0, inner_w - corner2), (corner2, corner2))
    p.paste(base_rb, (inner_w - corner2, inner_w - corner2), (corner2, corner2))
    p.paste(base_l.resize((corner2, inner_w - 2 * corner2)), (0, corner2))
    p.paste(base_r.resize((corner2, inner_w - 2 * corner2)), (inner_w - corner2, corner2))
    p.paste(base_t.resize((inner_w - 2 * corner2, corner2)), (corner2, 0))
    p.paste(base_b.resize((inner_w - 2 * corner2, corner2)), (corner2, inner_w - corner2))
    p.restore_region()

    p.paste(lb, (border2, w - border2 - lb.height))
    p.paste(rb, (w - border2 - rb.width, w - border2 - rb.height))
    p.paste(lt, (border2, border2))
    p.paste(rt, (w - border2 - rt.width, border2))
    p.paste(ct, ((w - ct.width) // 2, border2 - ct.height // 2))

    img = await p.get()
    img = resize_keep_ratio(img, frame_w / inner_w, mode="scale")
    return img


# 获取带框头像控件
async def get_avatar_widget_with_frame(
    is_frame: bool, frame_paths, avatar_img: Image.Image, avatar_w: int, frame_data: list[dict]
) -> Frame:
    frame_img = None
    if is_frame and frame_paths:
        frame_img = await get_player_frame_image(frame_paths, avatar_w + 5)

    with Frame().set_size((avatar_w, avatar_w)).set_content_align("c").set_allow_draw_outside(True) as ret:
        ImageBox(avatar_img, size=(avatar_w, avatar_w), use_alpha_blend=False)
        if frame_img:
            ImageBox(frame_img, use_alpha_blend=True)
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
        extra=extra or {"version": 1},
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
    lv_rank_bg = await get_img_from_path(ASSETS_BASE_DIR, ctx.request.lv_rank_bg_path)
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
    card_imgs = await asyncio.gather(*[get_card_full_thumbnail(card) for card in ctx.pcards])
    logger.debug("[perf] draw_main card_imgs %d: %.3fs", len(ctx.pcards), time.perf_counter() - _t0)
    for card_img in card_imgs:
        root.add_item(ImageBox(card_img, size=(90, 90), image_size_mode="fill", shadow=True))
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
        get_img_from_path(ASSETS_BASE_DIR, ctx.request.icon_clear_path),
        get_img_from_path(ASSETS_BASE_DIR, ctx.request.icon_fc_path),
        get_img_from_path(ASSETS_BASE_DIR, ctx.request.icon_ap_path),
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


async def _preload_profile_chara_icons(ctx: _ProfileLayoutContext) -> dict[str, Image.Image]:
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
        await asyncio.gather(*[get_img_from_path(ASSETS_BASE_DIR, path) for path in ordered_paths])
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
    chara_icon_cache: dict[str, Image.Image],
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
    chara_icon_cache: dict[str, Image.Image],
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
    avatar_img = await get_img_from_path(ASSETS_BASE_DIR, profile.leader_image_path)
    # 背景设置
    # 使用传入的背景图片，如果没有则使用默认蓝色背景
    bg_settings = rqd.bg_settings if rqd.bg_settings is not None else ProfileBgSettings()
    if bg_settings.img_path:
        try:
            bg_img = await get_img_from_path(ASSETS_BASE_DIR, bg_settings.img_path, on_missing="raise")
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


async def compose_profile_image(rqd: ProfileRequest) -> Image.Image:
    """合成个人信息图片 (Pillow 路径)。"""
    return await (await _build_profile_canvas(rqd)).get_img(_PROFILE_SCALE)


async def try_render_profile_payload(rqd: ProfileRequest) -> EncodedImagePayload | None:
    """Skia 路径：经 IRPainter 渲染同一棵 widget 树；不可用时返回 None 回退 Pillow。"""
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_profile_canvas(rqd), scale=_PROFILE_SCALE)


def _profile_card_data_source_label(name: str | None) -> str:
    if not name:
        return "数据"
    if name.endswith("数据"):
        return name[:-2]
    return name


async def _build_profile_card_avatar_module(rqd: ProfileCardRequest) -> Widget | None:
    if not rqd.profile:
        return None
    avatar_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.profile.leader_image_path)
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
