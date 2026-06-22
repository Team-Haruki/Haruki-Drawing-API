import asyncio
from dataclasses import dataclass
import logging
import math
import time
from typing import Any

from PIL import Image, ImageDraw

from src.sekai.base.draw import (
    BG_PADDING,
    CHARACTER_COLOR_CODE,
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
    color_code_to_rgb,
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

MODULAR_DIFF_LABELS = {
    "easy": "EASY",
    "normal": "NORM",
    "hard": "HARD",
    "expert": "EXPR",
    "master": "MAST",
    "append": "APPD",
}

# =========================== 从.model导入数据类型 =========================== #

from .model import (
    BasicProfile,
    CardFullThumbnailRequest,
    CharacterRank,
    ModularProfileRenderRequest,
    ModularProfileWidget,
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


def _format_modular_number(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _coerce_card_full_thumbnail(value: Any) -> CardFullThumbnailRequest | None:
    if isinstance(value, CardFullThumbnailRequest):
        return value
    if isinstance(value, dict):
        try:
            return CardFullThumbnailRequest.model_validate(value)
        except ValueError:
            logger.warning("skip invalid modular profile card payload: %s", value)
    return None


def _coerce_music_clear_count(value: Any) -> MusicClearCount | None:
    if isinstance(value, MusicClearCount):
        return value
    if isinstance(value, dict):
        try:
            return MusicClearCount.model_validate(value)
        except ValueError:
            logger.warning("skip invalid modular profile music count payload: %s", value)
    return None


def _coerce_character_rank(value: Any) -> CharacterRank | None:
    if isinstance(value, CharacterRank):
        return value
    if isinstance(value, dict):
        try:
            return CharacterRank.model_validate(value)
        except ValueError:
            logger.warning("skip invalid modular profile character rank payload: %s", value)
    return None


def _coerce_honor_request(value: Any) -> HonorRequest | None:
    if isinstance(value, HonorRequest):
        return value
    if isinstance(value, dict):
        try:
            return HonorRequest.model_validate(value)
        except ValueError:
            logger.warning("skip invalid modular profile honor payload: %s", value)
    return None


def _modular_character_groups(ranks: list[CharacterRank]) -> list[tuple[str, float]]:
    by_id = {rank.character_id: rank.rank for rank in ranks}
    groups = [
        ("VS", [21, 22, 23, 24, 25, 26]),
        ("LN", [1, 2, 3, 4]),
        ("MMJ", [5, 6, 7, 8]),
        ("VBS", [9, 10, 11, 12]),
        ("WxS", [13, 14, 15, 16]),
        ("25", [17, 18, 19, 20]),
    ]
    values: list[tuple[str, float]] = []
    for label, ids in groups:
        group_values = [by_id[cid] for cid in ids if cid in by_id]
        values.append((label, sum(group_values) / len(group_values) if group_values else 0))
    return values


def _modular_character_label(character_id: int) -> str:
    return CHARA_ID2NICKNAME.get(character_id, str(character_id)).upper()


def _modular_character_color(character_id: int) -> tuple[int, int, int, int]:
    code = CHARACTER_COLOR_CODE.get(character_id)
    if not code:
        return (*PLAY_RESULT_COLORS["fc"][:3], 220)
    color = color_code_to_rgb(code)
    return int(color[0]), int(color[1]), int(color[2]), 220


def _modular_character_text_color(color: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    brightness = color[0] * 0.299 + color[1] * 0.587 + color[2] * 0.114
    return BLACK if brightness > 178 else WHITE


def _build_modular_character_marker(
    character_id: int,
    *,
    width: int = 66,
    height: int = 30,
    font_size: int = 13,
) -> Widget:
    color = _modular_character_color(character_id)
    return (
        TextBox(
            _modular_character_label(character_id),
            TextStyle(font=DEFAULT_BOLD_FONT, size=font_size, color=_modular_character_text_color(color)),
        )
        .set_bg(RoundRectBg(fill=color, radius=height // 2))
        .set_size((width, height))
        .set_content_align("c")
        .set_padding(0)
        .set_wrap(False)
    )


def _modular_chara_icon_path_map(rqd: ModularProfileRenderRequest, widget: ModularProfileWidget | None = None) -> dict:
    raw_map = None
    if widget is not None:
        raw_map = widget.data.get("chara_rank_icon_path_map")
    if not raw_map:
        raw_map = rqd.resources.get("chara_rank_icon_path_map")
    return raw_map if isinstance(raw_map, dict) else {}


async def _build_modular_character_icon_marker(
    character_id: int,
    icon_map: dict,
    *,
    size: tuple[int, int] = (72, 36),
) -> Widget:
    path = icon_map.get(character_id) or icon_map.get(str(character_id))
    if path:
        try:
            icon = await get_img_from_path(ASSETS_BASE_DIR, path, on_missing="raise")
            return ImageBox(icon, size=size, use_alpha_blend=True)
        except (FileNotFoundError, OSError, ValueError):
            logger.warning("skip broken modular character icon: %s", path)
    return _build_modular_character_marker(character_id, width=size[0], height=size[1])


async def _build_modular_character_rank_icon_widget(
    character_id: int,
    rank: int,
    icon_map: dict,
    *,
    size: tuple[int, int] = (112, 56),
) -> Widget:
    path = icon_map.get(character_id) or icon_map.get(str(character_id))
    if not path:
        row = HSplit().set_item_align("c").set_content_align("c").set_sep(8)
        row.add_item(_build_modular_character_marker(character_id, width=64, height=32))
        row.add_item(TextBox(str(rank), TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=ADAPTIVE_WB)))
        return row

    try:
        icon = await get_img_from_path(ASSETS_BASE_DIR, path, on_missing="raise")
    except (FileNotFoundError, OSError, ValueError):
        logger.warning("skip broken modular character rank icon: %s", path)
        return await _build_modular_character_icon_marker(character_id, icon_map, size=size)

    width, height = size
    frame = Frame().set_size(size).set_content_align("c").set_allow_draw_outside(True)
    frame.add_item(ImageBox(icon, size=size, use_alpha_blend=True))
    rank_box_x = int(width * 36 / 96)
    rank_box_w = max(28, int(width * 60 / 96))
    frame.add_item(
        TextBox(str(rank), TextStyle(font=DEFAULT_BOLD_FONT, size=max(18, int(height * 0.48)), color=BLACK))
        .set_size((rank_box_w, height))
        .set_content_align("c")
        .set_padding(0)
        .set_offset((rank_box_x, max(0, int(height * 4 / 48))))
    )
    return frame


def _modular_card_bundle_name(card_thumbnail_path: str) -> tuple[str, str] | None:
    filename = card_thumbnail_path.replace("\\", "/").rsplit("/", 1)[-1]
    for state in ("after_training", "normal"):
        suffix = f"_{state}.png"
        if filename.endswith(suffix):
            return filename[: -len(suffix)], state
    return None


def _modular_card_art_candidates(card: CardFullThumbnailRequest) -> list[str]:
    normalized = card.card_thumbnail_path.replace("\\", "/").strip()
    bundle_state = _modular_card_bundle_name(normalized)
    if bundle_state is None:
        return []

    bundle, state = bundle_state
    if card.is_after_training is not None:
        state = "after_training" if card.is_after_training else "normal"
    filename = normalized.rsplit("/", 1)[-1]
    member_file = f"card_{state}.png"
    candidates: list[str] = []
    if "/thumbnail/chara/" in normalized:
        base = normalized.replace("/thumbnail/chara/", "/character/member/")
        candidates.append(base.replace(filename, f"{bundle}/{member_file}"))
        base_small = normalized.replace("/thumbnail/chara/", "/character/member_small/")
        candidates.append(base_small.replace(filename, f"{bundle}/{member_file}"))
    candidates.extend(
        [
            f"asset/cn-assets/startapp/character/member/{bundle}/{member_file}",
            f"asset/cn-assets/startapp/character/member_small/{bundle}/{member_file}",
        ]
    )
    return candidates


async def _load_modular_card_image(card: CardFullThumbnailRequest, *, prefer_full_art: bool) -> Image.Image:
    if prefer_full_art:
        for candidate in _modular_card_art_candidates(card):
            try:
                return await get_img_from_path(ASSETS_BASE_DIR, candidate, on_missing="raise")
            except (FileNotFoundError, OSError, ValueError):
                continue
    return await get_img_from_path(ASSETS_BASE_DIR, card.card_thumbnail_path)


def _modular_widget_rect(
    widget: ModularProfileWidget,
    *,
    cell_width: int,
    row_height: int,
    gutter: int,
    padding: int,
) -> tuple[int, int, int, int]:
    frame = widget.frame
    x = padding + max(0, frame.x) * (cell_width + gutter)
    y = padding + max(0, frame.y) * (row_height + gutter)
    width = max(1, frame.w) * cell_width + max(0, frame.w - 1) * gutter
    height = max(1, frame.h) * row_height + max(0, frame.h - 1) * gutter
    return x, y, width, height


def _round_modular_image(img: Image.Image, radius: int = 10) -> Image.Image:
    rounded = img.convert("RGBA").copy()
    mask = Image.new("L", rounded.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, rounded.width, rounded.height), radius=radius, fill=255)
    rounded.putalpha(mask)
    return rounded


class _ModularProfileGridCanvas(Widget):
    def __init__(self, size: tuple[int, int]) -> None:
        super().__init__()
        self.canvas_size = size
        self.positioned_items: list[tuple[Widget, tuple[int, int]]] = []
        self.set_size(size)
        self.set_padding(0)
        self.set_content_align("lt")

    def add_positioned_item(self, item: Widget, offset: tuple[int, int]) -> None:
        item.set_parent(self)
        self.positioned_items.append((item, offset))

    def _get_content_size(self) -> tuple[int, int]:
        return self.canvas_size

    def _draw_content(self, p: Painter) -> None:
        for item, (x, y) in self.positioned_items:
            w, h = item._get_self_size()
            p.move_region((x, y), (w, h))
            item.draw(p)
            p.restore_region()


def _modular_panel_content_size(size: tuple[int, int], title: str | None) -> tuple[int, int]:
    width, height = size
    pad_x = 16 if title else 12
    pad_y = 12 if title else 10
    title_h = 22 if title else 0
    title_gap = 6 if title else 0
    return max(1, width - pad_x * 2), max(1, height - pad_y * 2 - title_h - title_gap)


def _build_modular_profile_panel_widget(
    title: str | None,
    content: Widget,
    *,
    size: tuple[int, int],
    ui_bg: RoundRectBg,
) -> Widget:
    width, height = size
    content_w, content_h = _modular_panel_content_size(size, title)
    pad_x = 16 if title else 12
    pad_y = 12 if title else 10
    root = (
        VSplit()
        .set_bg(ui_bg)
        .set_size((width, height))
        .set_padding((pad_x, pad_y))
        .set_sep(6)
        .set_content_align("c")
        .set_item_align("c")
        .set_allow_draw_outside(True)
    )
    if title:
        root.add_item(
            TextBox(title, TextStyle(font=DEFAULT_BOLD_FONT, size=15, color=ADAPTIVE_WB))
            .set_size((content_w, 22))
            .set_content_align("l")
            .set_padding(0)
            .set_wrap(False)
            .set_omit_parent_bg(True)
        )
    content_frame = (
        Frame().set_size((content_w, content_h)).set_content_align("c").set_padding(0).set_allow_draw_outside(True)
    )
    content_frame.add_item(content)
    root.add_item(content_frame)
    return root


async def _build_modular_profile_summary_plot_widget(
    rqd: ModularProfileRenderRequest,
    widget: ModularProfileWidget,
    *,
    size: tuple[int, int],
    ui_bg: RoundRectBg,
) -> Widget:
    content_w, content_h = _modular_panel_content_size(size, widget.title)
    avatar_w = min(112, max(72, content_h - 4))
    avatar_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.profile.leader_image_path)
    avatar = await get_avatar_widget_with_frame(
        is_frame=bool(rqd.profile.has_frame),
        frame_paths=None,
        avatar_img=avatar_img,
        avatar_w=avatar_w,
        frame_data=[],
    )

    identity_w = max(180, content_w - avatar_w - 26)
    identity = VSplit().set_item_align("l").set_content_align("c").set_sep(8).set_w(identity_w)
    identity.add_item(
        colored_text_box(
            truncate(rqd.profile.nickname, 64),
            TextStyle(font=DEFAULT_BOLD_FONT, size=32, color=ADAPTIVE_WB, use_shadow=True, shadow_offset=2),
        )
    )
    uid = process_hide_uid(rqd.profile.is_hide_uid, rqd.profile.id, keep=6)
    identity.add_item(
        TextBox(
            f"{rqd.profile.region.upper()}: {uid}",
            TextStyle(font=DEFAULT_FONT, size=20, color=ADAPTIVE_WB),
            line_count=1,
        )
        .set_w(identity_w)
        .set_wrap(False)
        .set_padding(0)
    )
    lv_rank_bg_path = rqd.resources.get("lv_rank_bg_path")
    if lv_rank_bg_path:
        try:
            lv_rank_bg = await get_img_from_path(ASSETS_BASE_DIR, str(lv_rank_bg_path), on_missing="raise")
            badge_w = min(190, max(150, identity_w // 2))
            badge_h = max(1, int(lv_rank_bg.size[1] * badge_w / lv_rank_bg.size[0]))
            rank_badge = Frame().set_size((badge_w, badge_h))
            rank_badge.add_item(ImageBox(lv_rank_bg, size=(badge_w, badge_h)))
            number_box_x = int(badge_w * 104 / 180)
            number_box_w = max(44, badge_w - number_box_x - 8)
            rank_badge.add_item(
                TextBox(
                    f"{widget.data.get('rank') or 0}",
                    TextStyle(font=DEFAULT_FONT, size=max(24, int(badge_h * 0.6)), color=WHITE),
                )
                .set_size((number_box_w, badge_h))
                .set_padding(0)
                .set_wrap(False)
                .set_content_align("c")
                .set_offset((number_box_x, 0))
            )
            identity.add_item(rank_badge)
        except (FileNotFoundError, OSError, ValueError):
            logger.warning("skip broken modular profile rank bg: %s", lv_rank_bg_path)
            identity.add_item(
                _build_profile_stats_badge(f"Rank {_format_modular_number(widget.data.get('rank'))}", font_size=16)
            )
    else:
        identity.add_item(
            _build_profile_stats_badge(f"Rank {_format_modular_number(widget.data.get('rank'))}", font_size=16)
        )

    row = HSplit().set_content_align("c").set_item_align("c").set_sep(18)
    row.add_item(avatar)
    row.add_item(identity)
    return _build_modular_profile_panel_widget(widget.title, row, size=size, ui_bg=ui_bg)


async def _build_modular_deck_plot_widget(
    widget: ModularProfileWidget,
    *,
    size: tuple[int, int],
    ui_bg: RoundRectBg,
) -> Widget:
    content_w, content_h = _modular_panel_content_size(size, widget.title)
    raw_cards = widget.data.get("cards") or []
    cards = [_coerce_card_full_thumbnail(raw) for raw in raw_cards]
    cards = [card for card in cards if card is not None][:5]
    if not cards:
        content = TextBox("暂无队伍卡面", TextStyle(font=DEFAULT_FONT, size=20, color=ADAPTIVE_WB))
        return _build_modular_profile_panel_widget(widget.title, content, size=size, ui_bg=ui_bg)

    card_imgs = await asyncio.gather(*[get_card_full_thumbnail(card) for card in cards], return_exceptions=True)
    valid_imgs = [img for img in card_imgs if isinstance(img, Image.Image)]
    card_size = min(104, content_h, max(54, (content_w - 8 * max(0, len(valid_imgs) - 1)) // max(1, len(valid_imgs))))
    row = HSplit().set_content_align("c").set_item_align("c").set_sep(8)
    for card_img in valid_imgs:
        row.add_item(
            ImageBox(card_img, size=(card_size, card_size), image_size_mode="fill", shadow=True, shadow_width=4)
        )
    return _build_modular_profile_panel_widget(widget.title, row, size=size, ui_bg=ui_bg)


def _build_modular_fc_ap_plot_widget(
    widget: ModularProfileWidget,
    *,
    size: tuple[int, int],
    ui_bg: RoundRectBg,
) -> Widget:
    content_w, content_h = _modular_panel_content_size(size, widget.title)
    raw_counts = widget.data.get("counts") or []
    counts = [_coerce_music_clear_count(raw) for raw in raw_counts]
    diff_count = _build_profile_diff_count([count for count in counts if count is not None])

    hs, vs = 5, 6
    gw = max(34, min(70, (content_w - hs * 5) // 6))
    gh = max(18, min(24, (content_h - vs * 3) // 4))
    grid = Grid(col_count=6).set_sep(h_sep=hs, v_sep=vs)
    for diff, color in DIFF_COLORS.items():
        grid.add_item(
            TextBox(
                MODULAR_DIFF_LABELS.get(diff, diff.upper()[:4]),
                TextStyle(font=DEFAULT_BOLD_FONT, size=12, color=WHITE),
            )
            .set_bg(RoundRectBg(fill=color, radius=3))
            .set_size((gw, gh))
            .set_content_align("c")
        )

    for result_name in ["clear", "fc", "ap"]:
        for column, diff in enumerate(DIFF_COLORS.keys()):
            bg_color = (255, 255, 255, 150) if column % 2 == 0 else (255, 255, 255, 100)
            count = diff_count[diff][result_name]
            grid.add_item(
                TextBox(
                    str(count),
                    TextStyle(
                        DEFAULT_FONT,
                        16,
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
    return _build_modular_profile_panel_widget(widget.title, grid, size=size, ui_bg=ui_bg)


def _build_modular_radar_image(ranks: list[CharacterRank], size: tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    groups = _modular_character_groups(ranks)
    values = [value for _, value in groups]
    max_rank = max(100, int(max(values, default=0)))
    center = (width * 0.42, height * 0.43)
    radius = max(20, min(width, height) * 0.25)
    grid_color = (*PLAY_RESULT_COLORS["not_clear"][:3], 135)
    label_color = (*PLAY_RESULT_COLORS["not_clear"][:3], 230)
    fill_color = (*PLAY_RESULT_COLORS["fc"][:3], 80)
    outline_color = (*PLAY_RESULT_COLORS["fc"][:3], 235)
    for level in (1, 2, 3):
        points = []
        for idx in range(len(groups)):
            angle = -math.pi / 2 + idx * math.tau / len(groups)
            points.append(
                (
                    center[0] + math.cos(angle) * radius * level / 3,
                    center[1] + math.sin(angle) * radius * level / 3,
                )
            )
        draw.polygon(points, outline=grid_color)
    radar_points = []
    font = get_font(DEFAULT_BOLD_FONT, 12)
    for idx, (label, value) in enumerate(groups):
        angle = -math.pi / 2 + idx * math.tau / len(groups)
        end = (center[0] + math.cos(angle) * radius, center[1] + math.sin(angle) * radius)
        draw.line((center, end), fill=grid_color, width=1)
        label_pos = (center[0] + math.cos(angle) * (radius + 15), center[1] + math.sin(angle) * (radius + 15))
        text_w, text_h = get_text_size(font, label)
        draw.text((label_pos[0] - text_w / 2, label_pos[1] - text_h / 2), label, font=font, fill=label_color)
        ratio = min(1, value / max_rank)
        radar_points.append(
            (
                center[0] + math.cos(angle) * radius * ratio,
                center[1] + math.sin(angle) * radius * ratio,
            )
        )
    if radar_points:
        draw.polygon(radar_points, fill=fill_color, outline=outline_color)
    return image


def _build_modular_character_rank_marker_image(
    icon: Image.Image | None,
    character_id: int,
    rank: int,
    *,
    size: tuple[int, int],
) -> Image.Image:
    width, height = size
    if icon is None:
        color = _modular_character_color(character_id)
        marker = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(marker)
        draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=height // 2, fill=(*WHITE[:3], 205))
        draw.ellipse((1, 1, height - 2, height - 2), fill=color)
        text_color = _modular_character_text_color(color)
        label_font = get_font(DEFAULT_BOLD_FONT, max(9, int(height * 0.36)))
        draw.text(
            (height * 0.5, height * 0.5),
            _modular_character_label(character_id)[:2],
            font=label_font,
            fill=text_color,
            anchor="mm",
        )
    else:
        marker = icon.convert("RGBA").resize(size, Image.Resampling.BILINEAR)

    draw = ImageDraw.Draw(marker)
    rank_text = str(rank)
    font = get_font(DEFAULT_BOLD_FONT, max(11, int(height * 0.52)))
    text_w, text_h = get_text_size(font, rank_text)
    x = int(width * 0.69 - text_w / 2)
    y = int(height * 0.55 - text_h / 2)
    draw.text((x + 1, y + 1), rank_text, font=font, fill=(*WHITE[:3], 180))
    draw.text((x, y), rank_text, font=font, fill=BLACK)
    return marker


def _build_modular_full_character_radar_image(
    ranks: list[CharacterRank],
    icon_images: dict[int, Image.Image],
    size: tuple[int, int],
) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    chara_ids = [cid for _chara, cid in CHARA_LIST if cid is not None]
    rank_lookup = _build_profile_character_rank_lookup(ranks)
    values = [rank_lookup.get(cid, 1) for cid in chara_ids]
    max_rank = max(100, max(values, default=1))

    center = (width / 2, height / 2)
    icon_w, icon_h = 58, 29
    label_radius = max(80, min(width, height) * 0.42)
    radius = max(54, label_radius - 42)
    grid_color = (*PLAY_RESULT_COLORS["not_clear"][:3], 95)
    fill_color = (*PLAY_RESULT_COLORS["fc"][:3], 55)
    outline_color = (*PLAY_RESULT_COLORS["fc"][:3], 225)

    for level in (1, 2, 3, 4):
        level_radius = radius * level / 4
        points = []
        for idx in range(len(chara_ids)):
            angle = -math.pi / 2 + idx * math.tau / len(chara_ids)
            points.append(
                (
                    center[0] + math.cos(angle) * level_radius,
                    center[1] + math.sin(angle) * level_radius,
                )
            )
        draw.polygon(points, outline=grid_color)

    radar_points = []
    for idx, character_id in enumerate(chara_ids):
        angle = -math.pi / 2 + idx * math.tau / len(chara_ids)
        axis_end = (center[0] + math.cos(angle) * radius, center[1] + math.sin(angle) * radius)
        draw.line((center, axis_end), fill=grid_color, width=1)
        ratio = min(1, rank_lookup.get(character_id, 1) / max_rank)
        point = (center[0] + math.cos(angle) * radius * ratio, center[1] + math.sin(angle) * radius * ratio)
        radar_points.append(point)

        marker = _build_modular_character_rank_marker_image(
            icon_images.get(character_id),
            character_id,
            rank_lookup.get(character_id, 1),
            size=(icon_w, icon_h),
        )
        marker_x = int(center[0] + math.cos(angle) * label_radius - icon_w / 2)
        marker_y = int(center[1] + math.sin(angle) * label_radius - icon_h / 2)
        image.alpha_composite(marker, (marker_x, marker_y))

    if radar_points:
        draw.polygon(radar_points, fill=fill_color, outline=outline_color)
        for idx, point in enumerate(radar_points):
            point_color = _modular_character_color(chara_ids[idx])
            draw.ellipse(
                (point[0] - 4, point[1] - 4, point[0] + 4, point[1] + 4),
                fill=(*point_color[:3], 245),
                outline=(*WHITE[:3], 200),
            )
    return image


async def _build_modular_character_radar_plot_widget(
    rqd: ModularProfileRenderRequest,
    widget: ModularProfileWidget,
    *,
    size: tuple[int, int],
    ui_bg: RoundRectBg,
) -> Widget:
    content_w, content_h = _modular_panel_content_size(size, widget.title)
    raw_ranks = widget.data.get("character_rank") or []
    ranks = [_coerce_character_rank(raw) for raw in raw_ranks]
    ranks = [rank for rank in ranks if rank is not None]
    radar_img = _build_modular_radar_image(ranks, (min(170, content_w), content_h))
    stats = VSplit().set_item_align("c").set_content_align("c").set_sep(8)
    avg = int(sum(rank.rank for rank in ranks) / len(ranks)) if ranks else 0
    highest_rank = max(ranks, key=lambda rank: rank.rank, default=None)
    stats.add_item(_build_profile_stats_badge(f"AVG {avg}", font_size=16))
    if highest_rank is not None:
        stats.add_item(_build_profile_stats_badge("MAX", font_size=14, width=86))
        stats.add_item(
            await _build_modular_character_rank_icon_widget(
                highest_rank.character_id,
                highest_rank.rank,
                _modular_chara_icon_path_map(rqd, widget),
                size=(112, 56),
            )
        )
    else:
        stats.add_item(_build_profile_stats_badge("MAX 0", font_size=16))
    row = HSplit().set_content_align("c").set_item_align("c").set_sep(10)
    row.add_item(ImageBox(radar_img, image_size_mode="original", use_alpha_blend=True))
    row.add_item(stats)
    return _build_modular_profile_panel_widget(widget.title, row, size=size, ui_bg=ui_bg)


async def _build_modular_full_character_radar_plot_widget(
    rqd: ModularProfileRenderRequest,
    widget: ModularProfileWidget,
    *,
    size: tuple[int, int],
    ui_bg: RoundRectBg,
) -> Widget:
    content_w, content_h = _modular_panel_content_size(size, widget.title)
    raw_ranks = widget.data.get("character_rank") or []
    ranks = [_coerce_character_rank(raw) for raw in raw_ranks]
    ranks = [rank for rank in ranks if rank is not None]
    icon_map = _modular_chara_icon_path_map(rqd, widget)
    chara_ids = [cid for _chara, cid in CHARA_LIST if cid is not None]
    icon_images: dict[int, Image.Image] = {}
    for character_id in chara_ids:
        path = icon_map.get(character_id) or icon_map.get(str(character_id))
        if not path:
            continue
        try:
            icon_images[character_id] = await get_img_from_path(ASSETS_BASE_DIR, path, on_missing="raise")
        except (FileNotFoundError, OSError, ValueError):
            logger.warning("skip broken modular full radar icon: %s", path)
    radar_img = _build_modular_full_character_radar_image(ranks, icon_images, (content_w, content_h))
    content = ImageBox(radar_img, image_size_mode="original", use_alpha_blend=True)
    return _build_modular_profile_panel_widget(widget.title, content, size=size, ui_bg=ui_bg)


async def _build_modular_single_character_plot_widget(
    rqd: ModularProfileRenderRequest,
    widget: ModularProfileWidget,
    *,
    size: tuple[int, int],
    ui_bg: RoundRectBg,
) -> Widget:
    rank = _coerce_character_rank(widget.data.get("character_rank"))
    if rank is None:
        content = VSplit().set_item_align("c").set_content_align("c").set_sep(8)
        content.add_item(TextBox("未配置", TextStyle(font=DEFAULT_FONT, size=20, color=ADAPTIVE_WB)))
    else:
        content_w, content_h = _modular_panel_content_size(size, widget.title)
        content = Frame().set_size((content_w, content_h)).set_content_align("c").set_allow_draw_outside(True)
        icon_h = min(content_h, 86)
        icon_w = min(content_w, icon_h * 2)
        content.set_items(
            [
                await _build_modular_character_rank_icon_widget(
                    rank.character_id,
                    rank.rank,
                    _modular_chara_icon_path_map(rqd, widget),
                    size=(icon_w, icon_h),
                )
            ]
        )
    return _build_modular_profile_panel_widget(widget.title, content, size=size, ui_bg=ui_bg)


async def _build_modular_single_card_plot_widget(
    widget: ModularProfileWidget,
    *,
    size: tuple[int, int],
    ui_bg: RoundRectBg,
) -> Widget:
    content_w, content_h = _modular_panel_content_size(size, widget.title)
    card = _coerce_card_full_thumbnail(widget.data.get("card"))
    if card is None:
        content = TextBox("未配置", TextStyle(font=DEFAULT_FONT, size=20, color=ADAPTIVE_WB))
    else:
        prefer_full_art = size[0] >= size[1] or size[1] > size[0] * 1.35
        card_img = await _load_modular_card_image(card, prefer_full_art=prefer_full_art)
        if prefer_full_art:
            content = ImageBox(
                _round_modular_image(card_img, radius=10),
                size=(content_w, content_h),
                image_size_mode="fill",
                shadow=True,
                shadow_width=4,
            )
        else:
            card_size = min(104, content_w, content_h)
            content = ImageBox(
                card_img, size=(card_size, card_size), image_size_mode="fill", shadow=True, shadow_width=4
            )
    return _build_modular_profile_panel_widget(widget.title, content, size=size, ui_bg=ui_bg)


async def _build_modular_event_plot_widget(
    widget: ModularProfileWidget,
    *,
    size: tuple[int, int],
    ui_bg: RoundRectBg,
) -> Widget:
    content_w, content_h = _modular_panel_content_size(size, widget.title)
    rank = widget.data.get("rank")
    pt = widget.data.get("pt")
    banner_path = widget.data.get("banner_path")
    badge_text = widget.data.get("badge_text")
    event_honor = _coerce_honor_request(widget.data.get("event_honor") or widget.data.get("honor"))
    banner_img: Image.Image | None = None
    if banner_path:
        try:
            banner_img = await get_img_from_path(ASSETS_BASE_DIR, str(banner_path), on_missing="raise")
        except (FileNotFoundError, OSError, ValueError):
            logger.warning("skip broken modular event banner: %s", banner_path)

    stats = VSplit().set_item_align("l").set_content_align("c").set_sep(8)
    if event_honor is not None:
        try:
            honor_img = await compose_full_honor_image(event_honor)
            if honor_img is not None:
                stats.add_item(
                    ImageBox(
                        honor_img,
                        size=(min(200, content_w), None),
                        image_size_mode="fit",
                        shadow=True,
                        shadow_width=4,
                    )
                )
        except (FileNotFoundError, OSError, ValueError) as exc:
            logger.warning("skip broken modular event honor: %s", exc)

    if rank is None and pt is None:
        stats.add_item(_build_profile_stats_badge("待配置", font_size=18))
        stats.add_item(
            TextBox(
                "活动排名、PT 和结算时间后续从工具箱预设填入。",
                TextStyle(font=DEFAULT_FONT, size=15, color=ADAPTIVE_WB),
                line_count=2,
            )
            .set_w(max(120, min(content_w, 300)))
            .set_wrap(True)
        )
    else:
        stat_row = HSplit().set_item_align("c").set_content_align("l").set_sep(10)
        rank_badge = str(badge_text).strip() if badge_text else ""
        if not rank_badge and rank is not None:
            rank_badge = f"T{_format_modular_number(rank)}"
        if rank_badge:
            stat_row.add_item(_build_profile_stats_badge(rank_badge, font_size=16))
        stat_row.add_item(
            TextBox(
                f"#{_format_modular_number(rank)}",
                TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=ADAPTIVE_WB),
            )
        )
        stat_row.add_item(
            TextBox(
                f"{_format_modular_number(pt)} pt",
                TextStyle(font=DEFAULT_FONT, size=18, color=ADAPTIVE_WB),
            )
        )
        stats.add_item(stat_row)

    if banner_img is not None and content_w >= 440:
        stats_w = max(280, min(330, content_w // 2))
        banner_w = max(180, content_w - stats_w - 18)
        banner = ImageBox(
            _round_modular_image(banner_img, radius=8),
            size=(banner_w, content_h),
            image_size_mode="fill",
            shadow=True,
            shadow_width=4,
        )
        stats.set_w(stats_w)
        content = HSplit().set_item_align("c").set_content_align("c").set_sep(18)
        content.add_item(banner)
        content.add_item(stats)
    else:
        content = VSplit().set_item_align("l").set_content_align("c").set_sep(8)
        if banner_img is not None:
            content.add_item(
                ImageBox(
                    _round_modular_image(banner_img, radius=8),
                    size=(content_w, min(76, max(42, content_h // 2))),
                    image_size_mode="fill",
                    shadow=True,
                    shadow_width=4,
                )
            )
        content.add_item(stats)
    return _build_modular_profile_panel_widget(widget.title, content, size=size, ui_bg=ui_bg)


async def _build_modular_plot_widget(
    rqd: ModularProfileRenderRequest,
    widget: ModularProfileWidget,
    *,
    size: tuple[int, int],
    ui_bg: RoundRectBg,
) -> Widget:
    match widget.type:
        case "profile_summary":
            return await _build_modular_profile_summary_plot_widget(rqd, widget, size=size, ui_bg=ui_bg)
        case "deck_cards":
            return await _build_modular_deck_plot_widget(widget, size=size, ui_bg=ui_bg)
        case "fc_ap_clear":
            return _build_modular_fc_ap_plot_widget(widget, size=size, ui_bg=ui_bg)
        case "character_rank_radar":
            return await _build_modular_character_radar_plot_widget(rqd, widget, size=size, ui_bg=ui_bg)
        case "character_rank_full_radar":
            return await _build_modular_full_character_radar_plot_widget(rqd, widget, size=size, ui_bg=ui_bg)
        case "character_rank_board":
            return await _build_modular_full_character_radar_plot_widget(rqd, widget, size=size, ui_bg=ui_bg)
        case "single_character_rank":
            return await _build_modular_single_character_plot_widget(rqd, widget, size=size, ui_bg=ui_bg)
        case "single_card":
            return await _build_modular_single_card_plot_widget(widget, size=size, ui_bg=ui_bg)
        case "event_rank_pt":
            return await _build_modular_event_plot_widget(widget, size=size, ui_bg=ui_bg)
        case _:
            content = TextBox(widget.type, TextStyle(font=DEFAULT_FONT, size=18, color=ADAPTIVE_WB))
            return _build_modular_profile_panel_widget(widget.title, content, size=size, ui_bg=ui_bg)


async def compose_modular_profile_image(rqd: ModularProfileRenderRequest) -> Image.Image:
    r"""compose_modular_profile_image

    按 preset 中的网格和模块配置合成个人信息图片。
    """
    grid = rqd.preset.grid
    columns = max(1, grid.columns)
    row_height = max(80, grid.row_height)
    cell_width = max(80, grid.cell_width or grid.row_height)
    gutter = max(0, grid.gutter)
    padding = max(0, grid.padding)
    widgets = sorted(rqd.preset.widgets, key=lambda item: (item.frame.y, item.frame.x, item.id))
    rows = max((widget.frame.y + max(1, widget.frame.h) for widget in widgets), default=1)
    width = padding * 2 + columns * cell_width + max(0, columns - 1) * gutter
    height = padding * 2 + rows * row_height + max(0, rows - 1) * gutter

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
        fill=(255, 255, 255, bg_settings.alpha),
        blur_glass=True,
        blur_glass_kwargs={"blur": bg_settings.blur},
    )

    root = _ModularProfileGridCanvas((width, height))
    for widget in widgets:
        rect = _modular_widget_rect(
            widget,
            cell_width=cell_width,
            row_height=row_height,
            gutter=gutter,
            padding=padding,
        )
        module = await _build_modular_plot_widget(rqd, widget, size=(rect[2], rect[3]), ui_bg=ui_bg)
        root.add_positioned_item(module, (rect[0], rect[1]))

    canvas = Canvas(bg=bg).set_padding(0)
    canvas.add_item(root)
    add_request_watermark(
        canvas,
        rqd,
        extra_suffix="This background is user-uploaded." if bg_settings.img_path else None,
    )
    return await canvas.get_img(1.5)


async def compose_profile_image(rqd: ProfileRequest) -> Image.Image:
    r"""compose_profile_image

    合成个人信息图片
    Args
    ----
    rqd : ProfileRequest
        合成个人信息图片所必须的数据

    Returns
    -------
    PIL.Image.Image
    """
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
    return await canvas.get_img(1.5)


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
