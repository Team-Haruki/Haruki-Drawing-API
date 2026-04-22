import asyncio
import logging
import time

from PIL import Image, ImageDraw

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
    put_composed_image_cache,
    put_composed_image_disk_cache,
    run_in_pool,
    get_str_display_length,
    truncate,
)
from src.sekai.honor.drawer import compose_full_honor_image
from src.settings import ASSETS_BASE_DIR

logger = logging.getLogger(__name__)


def format_info_panel_update_time(update_time, timezone_name: str | None) -> str:
    timezone_label = (timezone_name or "").strip()
    if not timezone_label and update_time.tzinfo is not None:
        timezone_label = update_time.tzname() or ""

    text = update_time.strftime("%m-%d %H:%M:%S")
    if timezone_label:
        text += f" ({timezone_label})"
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
    CardFullThumbnailRequest,
    ProfileBgSettings,
    ProfileCardRequest,
    ProfileRequest,
)


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
    # 歌曲完成情况
    diff_count = {diff: {"clear": 0, "fc": 0, "ap": 0} for diff in DIFF_COLORS.keys()}
    for count in rqd.music_difficulty_count:
        if count.difficulty in diff_count:
            diff_count[count.difficulty] = {"clear": count.clear, "fc": count.fc, "ap": count.ap}
    # 角色等级
    character_rank = {cid: 1 for _, cid in CHARA_LIST if cid is not None}
    for crank in rqd.character_rank:
        character_rank[crank.character_id] = crank.rank

    # 挑战live / 协力统计
    solo_live = rqd.solo_live
    multi_live = rqd.multi_live

    # 个人信息部分
    async def draw_info():
        with VSplit().set_bg(ui_bg).set_content_align("c").set_item_align("c").set_sep(32).set_padding((32, 35)) as ret:
            # 名片
            with HSplit().set_content_align("c").set_item_align("c").set_sep(32).set_padding((32, 0)):
                has_frame = rqd.profile.has_frame
                avatar_widget = await get_avatar_widget_with_frame(  # noqa: F841
                    is_frame=bool(has_frame),
                    frame_paths=rqd.frame_paths,
                    avatar_img=avatar_img,
                    avatar_w=128,
                    frame_data=[],
                )
                with VSplit().set_content_align("c").set_item_align("l").set_sep(16):
                    colored_text_box(
                        truncate(rqd.profile.nickname, 64),
                        TextStyle(font=DEFAULT_BOLD_FONT, size=32, color=ADAPTIVE_WB, use_shadow=True, shadow_offset=2),
                    )
                    TextBox(
                        f"{profile.region.upper()}: {process_hide_uid(profile.is_hide_uid, profile.id, keep=6)}",
                        TextStyle(font=DEFAULT_FONT, size=20, color=ADAPTIVE_WB),
                    )
                    lv_rank_bg = await get_img_from_path(ASSETS_BASE_DIR, rqd.lv_rank_bg_path)
                    badge_w = 180
                    badge_h = max(1, int(lv_rank_bg.size[1] * badge_w / lv_rank_bg.size[0]))
                    number_box_x = 104
                    number_box_w = max(48, badge_w - number_box_x - 10)
                    with Frame().set_size((badge_w, badge_h)):
                        ImageBox(lv_rank_bg, size=(badge_w, badge_h))
                        (
                            TextBox(
                                f"{rqd.rank}",
                                TextStyle(font=DEFAULT_FONT, size=30, color=WHITE),
                            )
                            .set_size((number_box_w, badge_h))
                            .set_padding(0)
                            .set_wrap(False)
                            .set_content_align("c")
                            .set_offset((number_box_x, 0))
                        )

            # 推特
            with Frame().set_content_align("l").set_w(450):
                tw_id = rqd.twitter_id
                tw_id_box = TextBox(
                    "        @ " + tw_id, TextStyle(font=DEFAULT_FONT, size=20, color=ADAPTIVE_WB), line_count=1
                )
                tw_id_box.set_wrap(False).set_bg(ui_bg).set_line_sep(2).set_padding(10).set_w(300).set_content_align(
                    "l"
                )
                x_icon = await get_img_resized(ASSETS_BASE_DIR, rqd.x_icon_path, 24, 24)
                x_icon = x_icon.convert("RGBA")
                ImageBox(x_icon, image_size_mode="original").set_offset((16, 0))

            # 留言
            user_word = rqd.word
            user_word_box = ColoredTextBox(
                user_word, TextStyle(font=DEFAULT_FONT, size=20, color=ADAPTIVE_WB), line_count=3
            )
            user_word_box.set_wrap(True).set_bg(ui_bg).set_line_sep(2).set_padding((18, 16)).set_w(450)

            # 头衔
            with HSplit().set_content_align("c").set_item_align("c").set_sep(8).set_padding((16, 0)):
                honor_imgs = await asyncio.gather(
                    *[compose_full_honor_image(honor) for honor in honors], return_exceptions=True
                )
                for img in honor_imgs:
                    if isinstance(img, Exception):
                        logger.warning("skip broken honor asset in profile image: %s", img)
                        continue
                    if img:
                        ImageBox(img, size=(None, 48), shadow=True)
            # 卡组
            with HSplit().set_content_align("c").set_item_align("c").set_sep(6).set_padding((16, 0)):
                _t0 = time.perf_counter()
                card_imgs = await asyncio.gather(*[get_card_full_thumbnail(card) for card in pcards])
                logger.debug("[perf] draw_main card_imgs %d: %.3fs", len(pcards), time.perf_counter() - _t0)
                for i in range(len(card_imgs)):
                    ImageBox(card_imgs[i], size=(90, 90), image_size_mode="fill", shadow=True)
        return ret

    # 打歌部分
    async def draw_play():
        with HSplit().set_content_align("c").set_item_align("t").set_sep(12).set_bg(ui_bg).set_padding(32) as ret:
            hs, vs, gw, gh = 8, 12, 90, 25
            with VSplit().set_sep(vs):
                Spacer(gh, gh)
                _t0 = time.perf_counter()
                icon_clear, icon_fc, icon_ap = await asyncio.gather(
                    get_img_from_path(ASSETS_BASE_DIR, rqd.icon_clear_path),
                    get_img_from_path(ASSETS_BASE_DIR, rqd.icon_fc_path),
                    get_img_from_path(ASSETS_BASE_DIR, rqd.icon_ap_path),
                )
                logger.debug("[perf] draw_play play icons 3: %.3fs", time.perf_counter() - _t0)
                ImageBox(icon_clear, size=(gh, gh))
                ImageBox(icon_fc, size=(gh, gh))
                ImageBox(icon_ap, size=(gh, gh))
            with Grid(col_count=6).set_sep(h_sep=hs, v_sep=vs):
                for diff, color in DIFF_COLORS.items():
                    t = TextBox(diff.upper(), TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=WHITE))
                    t.set_bg(RoundRectBg(fill=color, radius=3)).set_size((gw, gh)).set_content_align("c")

                play_result = ["clear", "fc", "ap"]
                for i, score in enumerate(play_result):
                    for j, diff in enumerate(DIFF_COLORS.keys()):
                        bg_color = (255, 255, 255, 150) if j % 2 == 0 else (255, 255, 255, 100)
                        count = diff_count[diff][score]
                        TextBox(
                            str(count),
                            TextStyle(
                                DEFAULT_FONT,
                                20,
                                PLAY_RESULT_COLORS["not_clear"],
                                use_shadow=True,
                                shadow_color=PLAY_RESULT_COLORS[play_result[i]],
                                shadow_offset=1,
                            ),
                        ).set_bg(RoundRectBg(fill=bg_color, radius=3)).set_size((gw, gh)).set_content_align("c")
        return ret

    # 养成部分
    async def draw_chara():
        # 预加载所有角色等级图标（并行）
        chara_map = rqd.chara_rank_icon_path_map
        _chara_paths: dict[str, str] = {}
        for chara, cid in CHARA_LIST:
            if chara is None:
                continue
            p = chara_map.get(cid) or chara_map.get(str(cid))
            if p and p not in _chara_paths:
                _chara_paths[p] = p
        if solo_live is not None:
            scid = solo_live.character_id
            p = chara_map.get(scid) or chara_map.get(str(scid))
            if p and p not in _chara_paths:
                _chara_paths[p] = p
        _cp_list = list(_chara_paths.keys())
        _t0 = time.perf_counter()
        _cp_imgs = await asyncio.gather(*[get_img_from_path(ASSETS_BASE_DIR, p) for p in _cp_list]) if _cp_list else []
        logger.debug("[perf] draw_chara chara icons %d: %.3fs", len(_cp_list), time.perf_counter() - _t0)
        _chara_icon_cache = dict(zip(_cp_list, _cp_imgs))

        with Frame().set_content_align("rb").set_bg(ui_bg) as ret:
            hs, vs, gw, gh = 8, 7, 96, 48
            # 左侧：角色等级
            with Grid(col_count=6).set_sep(h_sep=hs, v_sep=vs).set_padding(32):
                for chara, cid in CHARA_LIST:
                    if chara is None:
                        Spacer(gw, gh)
                        continue
                    rank = character_rank[cid]
                    with Frame().set_size((gw, gh)):
                        c_rank_path = chara_map.get(cid) or chara_map.get(str(cid))
                        if not c_rank_path:
                            Spacer(gw, gh)
                            continue
                        chara_img = _chara_icon_cache[c_rank_path]
                        ImageBox(chara_img, size=(gw, gh), use_alpha_blend=True)
                        t = TextBox(str(rank), TextStyle(font=DEFAULT_FONT, size=20, color=(40, 40, 40, 255)))
                        t.set_size((60, 48)).set_content_align("c").set_offset((36, 4))

            # 右侧：Challenge Live + Multi Live
            if solo_live is not None or multi_live is not None:
                with VSplit().set_content_align("c").set_item_align("c").set_padding((50, 36)).set_sep(9):
                    common_style = TextStyle(font=DEFAULT_FONT, size=18, color=(50, 50, 50, 255))
                    box_bg = roundrect_bg(radius=6, alpha=80)
                    box_padding = (10, 7)

                    if solo_live is not None:
                        TextBox("CHALLENGE LIVE", common_style).set_bg(box_bg).set_padding(box_padding)

                        with Frame():
                            scid = solo_live.character_id
                            c_rank_path = chara_map.get(scid) or chara_map.get(str(scid))
                            if c_rank_path:
                                chara_img = _chara_icon_cache[c_rank_path]
                                ImageBox(chara_img, size=(100, 50), use_alpha_blend=True)
                            else:
                                Spacer(100, 50)
                            t = TextBox(
                                str(solo_live.rank),
                                TextStyle(font=DEFAULT_FONT, size=22, color=(40, 40, 40, 255)),
                                overflow="clip",
                            )
                            t.set_size((50, 50)).set_content_align("c").set_offset((40, 5))

                        TextBox(f"SCORE  {solo_live.score}", common_style).set_bg(box_bg).set_padding(box_padding)

                    if multi_live is not None:
                        TextBox("MULTI LIVE", common_style).set_bg(box_bg).set_padding(box_padding)
                        TextBox(f"MVP  {multi_live.mvp}次", common_style).set_bg(box_bg).set_padding(box_padding)
                        TextBox(f"SUPERSTAR  {multi_live.super_star}次", common_style).set_bg(box_bg).set_padding(box_padding)
        return ret

    vertical = bg_settings.vertical

    with Canvas(bg=bg).set_padding(BG_PADDING) as canvas:
        if not vertical:
            with HSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
                await draw_info()
                with VSplit().set_content_align("c").set_item_align("c").set_sep(16):
                    await draw_play()
                    await draw_chara()
        else:
            with VSplit().set_content_align("c").set_item_align("c").set_sep(16).set_item_bg(ui_bg):
                (await draw_info()).set_bg(None)
                (await draw_play()).set_bg(None)
                (await draw_chara()).set_bg(None)

    add_request_watermark(
        canvas,
        rqd,
        extra_suffix="This background is user-uploaded." if bg_settings.img_path else None,
    )
    return await canvas.get_img(1.5)


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

    def data_source_label(name: str | None) -> str:
        if not name:
            return "数据"
        if name.endswith("数据"):
            return name[:-2]
        return name

    with Frame().set_bg(roundrect_bg(alpha=bg_alpha)).set_padding(16) as f:  # noqa: F841
        with HSplit().set_content_align("c").set_item_align("c").set_sep(14):
            # 个人信息
            if rqd.profile:
                # 框
                has_frame = rqd.profile.has_frame
                avatar_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.profile.leader_image_path)
                # 头像
                avatar_widget = await get_avatar_widget_with_frame(  # noqa: F841
                    is_frame=bool(has_frame),
                    frame_paths=None,  # ProfileCardRequest 不支持 frame_paths
                    avatar_img=avatar_img,
                    avatar_w=80,
                    frame_data=[],
                )
                data_sources = [item for item in rqd.data_sources if item]
                primary_source = data_sources[0] if data_sources else None
                with VSplit().set_content_align("c").set_item_align("l").set_sep(5):
                    # 昵称、id和区服
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
                            ms_lv_text = (
                                f"MySekai Lv.{rqd.mysekai_level}"
                                if name_length <= 12
                                else f"MSLv.{rqd.mysekai_level}"
                            )
                            TextBox(ms_lv_text, TextStyle(font=DEFAULT_FONT, size=18, color=BLACK))
                    user_id = process_hide_uid(rqd.profile.is_hide_uid, rqd.profile.id, keep=6)
                    summary_line = f"{rqd.profile.region.upper()}: {user_id}"
                    if len(data_sources) <= 1 and primary_source and primary_source.name:
                        summary_line += f" {primary_source.name}"
                    TextBox(
                        summary_line,
                        TextStyle(font=DEFAULT_FONT, size=16, color=BLACK),
                    )
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
                                f"{data_source_label(data_source.name)}更新时间: {update_time_text}",
                                TextStyle(font=DEFAULT_FONT, size=16, color=BLACK),
                            )
            # 错误/警告
            if rqd.error_message:
                TextBox(rqd.error_message, TextStyle(font=DEFAULT_FONT, size=20, color=RED), line_count=3).set_w(300)
    return f
