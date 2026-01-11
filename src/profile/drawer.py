from datetime import datetime
from PIL import Image, ImageDraw
from src.base.configs import ASSETS_BASE_DIR, RESULT_ASSET_PATH
from src.base.utils import get_img_from_path
from src.base.utils import get_readable_datetime, truncate
from src.base.painter import(
    DEFAULT_FONT,
    DEFAULT_BOLD_FONT,
    BLACK,
    resize_keep_ratio,
    Painter,
    get_font,
    WHITE,
    ADAPTIVE_WB,
    RED
)
from src.base.plot import (
    Frame,
    HSplit,
    VSplit,
    TextStyle,
    TextBox,
    colored_text_box,
    ImageBox,
    Spacer,
    Grid,
    RoundRectBg,
    Canvas,
    ImageBg,
)
from src.base.draw import (
    roundrect_bg, 
    DIFF_COLORS, 
    PLAY_RESULT_COLORS,
    BG_PADDING,
    add_watermark,
    DEFAULT_WATERMARK,
    SEKAI_BLUE_BG
)
import re
from src.honor.drawer import compose_full_honor_image
import asyncio

# =========================== 从.model导入数据类型 =========================== #

from .model import *

async def get_card_full_thumbnail(rqd: CardFullThumbnailRequest) -> Image.Image:
    img = await get_img_from_path(ASSETS_BASE_DIR, rqd.card_thumbnail_path)
    rare_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.rare_img_path)
    if rqd.rare == "rarity_birthday":
        rare_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.birthday_icon_path)
        rare_num = 1
    else:
        rare_num = int(rqd.rare.split("_")[1])

    img_w, img_h = img.size
    custom_text = None
    if rqd.custom_text:
        custom_text = rqd.custom_text
    pcard= rqd.is_pcard
    # 如果是profile卡片则绘制等级/加成
    if pcard:
        if custom_text is not None:
            draw = ImageDraw.Draw(img)
            draw.rectangle((0, img_h - 24, img_w, img_h), fill=(70, 70, 100, 255))
            draw.text((6, img_h - 31), custom_text, font=get_font(DEFAULT_BOLD_FONT, 20), fill=WHITE)
        else:
            level = rqd.level
            draw = ImageDraw.Draw(img)
            draw.rectangle((0, img_h - 24, img_w, img_h), fill=(70, 70, 100, 255))
            draw.text((6, img_h - 31), f"Lv.{level}", font=get_font(DEFAULT_BOLD_FONT, 20), fill=WHITE)

    # 绘制边框
    frame_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.frame_img_path)
    frame_img = frame_img.resize((img_w, img_h))
    img.paste(frame_img, (0, 0), frame_img)
    # 绘制特训等级
    if pcard:
        rank = rqd.train_rank
        if rank:
            rank_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.train_rank_img_path)
            rank_img = rank_img.resize((int(img_w * 0.35), int(img_h * 0.35)))
            rank_img_w, rank_img_h = rank_img.size
            img.paste(rank_img, (img_w - rank_img_w, img_h - rank_img_h), rank_img)
    # 左上角绘制属性
    attr_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.attr_img_path)
    attr_img = attr_img.resize((int(img_w * 0.22), int(img_h * 0.25)))
    img.paste(attr_img, (1, 0), attr_img)
    # 左下角绘制稀有度
    hoffset, voffset = 6, 6 if not pcard else 24
    scale = 0.17 if not pcard else 0.15
    rare_img = rare_img.resize((int(img_w * scale), int(img_h * scale)))
    rare_w, rare_h = rare_img.size
    for i in range(rare_num):
        img.paste(rare_img, (hoffset + rare_w * i, img_h - rare_h - voffset), rare_img)
    mask = Image.new("L", (img_w, img_h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, img_w, img_h), radius=10, fill=255)
    img.putalpha(mask)

    return img

# 获取头像框图片，失败返回None
async def get_player_frame_image(frame_path: str, frame_w: int) -> Image.Image | None:
    frame_base_path = ASSETS_BASE_DIR.joinpath(frame_path)
    scale = 1.5
    corner = 20
    corner2 = 50
    w = 700
    border = 100
    border2 = 80
    inner_w = w - 2 * border

    base = await get_img_from_path(frame_base_path, "horizontal/frame_base.png")
    ct = await get_img_from_path(frame_base_path,"vertical/frame_centertop.png")
    lb = await get_img_from_path(frame_base_path,"vertical/frame_leftbottom.png")
    lt = await get_img_from_path(frame_base_path, "vertical/frame_lefttop.png")
    rb = await get_img_from_path(frame_base_path, "vertical/frame_rightbottom.png")
    rt = await get_img_from_path(frame_base_path, "vertical/frame_righttop.png")

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
async def get_avatar_widget_with_frame(is_frame: bool, frame_path: str, avatar_img: Image.Image, avatar_w: int, frame_data: list[dict]) -> Frame:
    frame_img = None
    if is_frame:
        frame_img = await get_player_frame_image(frame_path ,avatar_w + 5)

    with Frame().set_size((avatar_w, avatar_w)).set_content_align('c').set_allow_draw_outside(True) as ret:
        ImageBox(avatar_img, size=(avatar_w, avatar_w), use_alpha_blend=False)
        if frame_img:
            ImageBox(frame_img, use_alpha_blend=True)
    return ret

def process_hide_uid(is_hide_uid: bool, uid: str, keep: int=0) -> str:
    if is_hide_uid:
        if keep:
            return "*" * (16 - keep) + str(uid)[-keep:]
        return "*" * 16
    return uid

async def get_detailed_profile_card(rqd: DetailedProfileCardRequest) -> Frame:
    profile = rqd
    with Frame().set_bg(roundrect_bg(alpha=80)).set_padding(16) as f:
        with HSplit().set_content_align('c').set_item_align('c').set_sep(14):
            if profile:
                mode = profile.mode
                frame_path = profile.frame_path
                has_frame = profile.has_frame
                avatar_img = await get_img_from_path(ASSETS_BASE_DIR, profile.leader_image_path)
                avatar_widget = await get_avatar_widget_with_frame(
                    is_frame=bool(has_frame),
                    frame_path=frame_path,
                    avatar_img=avatar_img,
                    avatar_w=80,
                    frame_data=[]
                )
                with VSplit().set_content_align('c').set_item_align('l').set_sep(5):
                    source = profile.source or "?"
                    update_time = datetime.fromtimestamp(profile.update_time / 1000)
                    update_time_text = update_time.strftime('%m-%d %H:%M:%S') + f" ({get_readable_datetime(update_time, show_original_time=False)})"
                    user_id = process_hide_uid(profile.is_hide_uid, profile.id, keep=6)
                    colored_text_box(
                        truncate(profile.nickname, 64),
                        TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK, use_shadow=True, shadow_offset=2),
                    )
                    TextBox(f"{profile.region.upper()}: {user_id} Suite数据", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
                    TextBox(f"更新时间: {update_time_text}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
                    TextBox(f"数据来源: {source}  获取模式: {mode}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
    return f
# 获取玩家基本信息的简单卡片控件，返回Frame
async def get_basic_profile_card(profile: BasicProfile) -> Frame:
    with Frame().set_bg(roundrect_bg(alpha=80)).set_padding(16) as f:
        with HSplit().set_content_align('c').set_item_align('c').set_sep(14):
            frame_path = profile.frame_path
            has_frame = profile.has_frame
            avatar_img = await get_img_from_path(ASSETS_BASE_DIR, profile.leader_image_path)
            avatar_widget = await get_avatar_widget_with_frame(
                is_frame=bool(has_frame),
                frame_path=frame_path,
                avatar_img=avatar_img,
                avatar_w=80,
                frame_data=[]
            )
            with VSplit().set_content_align('c').set_item_align('l').set_sep(5):
                user_id = process_hide_uid(profile.is_hide_uid, profile.id, keep=6)
                colored_text_box(
                    truncate(profile.nickname, 64),
                    TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK, use_shadow=True, shadow_offset=2),
                )
                TextBox(f"{profile.region.upper()}: {user_id}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
    return f

# 统一的合成玩家

async def compose_profile_image(
    rqd: ProfileRequest
) -> Image.Image:
    r"""compose_profile_image

    合成个人信息图片

    TODO:
    -----
    TextBox shadow 暂未实现，
    AdaptiveTextColor 自适应文字颜色暂未实现

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
            bg_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.background_image_path)
            bg = ImageBg(bg_img, blur=False,fade=0)
        except FileNotFoundError:
            bg = SEKAI_BLUE_BG
    else:
        bg = SEKAI_BLUE_BG
    ui_bg = roundrect_bg(fill=(255, 255, 255, bg_settings.alpha), blur_glass=True, blur_glass_kwargs={'blur': bg_settings.blur})
    # 称号
    honors = rqd.honors
    # 歌曲完成情况
    diff_count = {}
    for count in rqd.music_difficulty_count:
        diff_count[count.difficulty] = {
            "clear": count.clear,
            "fc": count.fc,
            "ap": count.ap
        }
    # 角色等级
    character_rank = {}
    for crank in rqd.character_rank:
        character_rank[crank.character_id] = crank.rank
        
    # 挑战live等级
    solo_live = rqd.solo_live
    # 个人信息部分
    async def draw_info():
        with VSplit().set_bg(ui_bg).set_content_align('c').set_item_align('c').set_sep(32).set_padding((32, 35)) as ret:
            # 名片
            with HSplit().set_content_align('c').set_item_align('c').set_sep(32).set_padding((32, 0)):
                frame_path = rqd.profile.frame_path
                has_frame = rqd.profile.has_frame
                avatar_widget = await get_avatar_widget_with_frame(
                    is_frame=bool(has_frame),
                    frame_path=frame_path,
                    avatar_img=avatar_img,
                    avatar_w=128,
                    frame_data=[]
                )
                with VSplit().set_content_align('c').set_item_align('l').set_sep(16):
                    colored_text_box(
                        truncate(rqd.profile.nickname, 64),
                        TextStyle(font=DEFAULT_BOLD_FONT, size=32, color=ADAPTIVE_WB, use_shadow=True, shadow_offset=2),
                    )
                    TextBox(f"{profile.region.upper()}: {process_hide_uid(profile.is_hide_uid, profile.id, keep=6)}", TextStyle(font=DEFAULT_FONT, size=20, color=ADAPTIVE_WB))
                    with Frame():
                        lv_rank_bg = await get_img_from_path(ASSETS_BASE_DIR, f"{RESULT_ASSET_PATH}/lv_rank_bg.png")
                        ImageBox(lv_rank_bg, size=(180, None))
                        TextBox(f"{rqd.rank}", TextStyle(font=DEFAULT_FONT, size=30, color=WHITE)).set_offset((110, 0))

            # 推特
            with Frame().set_content_align('l').set_w(450):
                tw_id = rqd.twitter_id
                tw_id_box = TextBox('        @ ' + tw_id, TextStyle(font=DEFAULT_FONT, size=20, color=ADAPTIVE_WB), line_count=1)
                tw_id_box.set_wrap(False).set_bg(ui_bg).set_line_sep(2).set_padding(10).set_w(300).set_content_align('l')
                x_icon = await get_img_from_path(ASSETS_BASE_DIR, f"{RESULT_ASSET_PATH}/x_icon.png")
                x_icon = x_icon.resize((24, 24)).convert('RGBA')
                ImageBox(x_icon, image_size_mode='original').set_offset((16, 0))

            # 留言
            user_word = rqd.word
            user_word = re.sub(r'<#.*?>', '', user_word)
            user_word_box = TextBox(user_word, TextStyle(font=DEFAULT_FONT, size=20, color=ADAPTIVE_WB), line_count=3)
            user_word_box.set_wrap(True).set_bg(ui_bg).set_line_sep(2).set_padding((18, 16)).set_w(450)

            # 头衔
            with HSplit().set_content_align('c').set_item_align('c').set_sep(8).set_padding((16, 0)):
                honor_imgs = await asyncio.gather(*[
                    compose_full_honor_image(honor) for honor in honors
                    ])
                for img in honor_imgs:
                    if img: 
                        ImageBox(img, size=(None, 48), shadow=True)
            # 卡组
            with HSplit().set_content_align('c').set_item_align('c').set_sep(6).set_padding((16, 0)):
                card_imgs = [
                    await get_card_full_thumbnail(card)
                    for card in pcards
                ]
                for i in range(len(card_imgs)):
                    ImageBox(card_imgs[i], size=(90, 90), image_size_mode='fill', shadow=True)
        return ret

    # 打歌部分
    async def draw_play(): 
        with HSplit().set_content_align('c').set_item_align('t').set_sep(12).set_bg(ui_bg).set_padding(32) as ret:
            hs, vs, gw, gh = 8, 12, 90, 25
            with VSplit().set_sep(vs):
                Spacer(gh, gh)
                icon_clear = await get_img_from_path(ASSETS_BASE_DIR, f"{RESULT_ASSET_PATH}/icon_clear.png")
                icon_fc = await get_img_from_path(ASSETS_BASE_DIR, f"{RESULT_ASSET_PATH}/icon_fc.png")
                icon_ap = await get_img_from_path(ASSETS_BASE_DIR, f"{RESULT_ASSET_PATH}/icon_ap.png")
                ImageBox(icon_clear, size=(gh, gh))
                ImageBox(icon_fc, size=(gh, gh))
                ImageBox(icon_ap, size=(gh, gh))
            with Grid(col_count=6).set_sep(h_sep=hs, v_sep=vs):
                for diff, color in DIFF_COLORS.items():
                    t = TextBox(diff.upper(), TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=WHITE))
                    t.set_bg(RoundRectBg(fill=color, radius=3)).set_size((gw, gh)).set_content_align('c')
                
                
                play_result = ['clear', 'fc', 'ap']
                for i, score in enumerate(play_result):
                    for j, diff in enumerate(DIFF_COLORS.keys()):
                        bg_color = (255, 255, 255, 150) if j % 2 == 0 else (255, 255, 255, 100)
                        count = diff_count[diff][score]        
                        TextBox(str(count), TextStyle(
                                DEFAULT_FONT, 20, PLAY_RESULT_COLORS['not_clear'], use_shadow=True,
                                shadow_color=PLAY_RESULT_COLORS[play_result[i]], shadow_offset=1,
                            )).set_bg(RoundRectBg(fill=bg_color, radius=3)).set_size((gw, gh)).set_content_align('c')
        return ret
    
    # 养成部分
    async def draw_chara():
        with Frame().set_content_align('rb').set_bg(ui_bg) as ret:
            hs, vs, gw, gh = 8, 7, 96, 48
            # 角色等级
            with Grid(col_count=6).set_sep(h_sep=hs, v_sep=vs).set_padding(32):
                chara_list = [
                    ("miku", 21), ("rin", 22), ("len", 23), ("luka", 24), ("meiko", 25), ("kaito", 26), 
                    ("ick", 1), ("saki", 2), ("hnm", 3), ("shiho", 4), (None, None), (None, None),
                    ("mnr", 5), ("hrk", 6), ("airi", 7), ("szk", 8), (None, None), (None, None),
                    ("khn", 9), ("an", 10), ("akt", 11), ("toya", 12), (None, None), (None, None),
                    ("tks", 13), ("emu", 14), ("nene", 15), ("rui", 16), (None, None), (None, None),
                    ("knd", 17), ("mfy", 18), ("ena", 19), ("mzk", 20), (None, None), (None, None),
                ]

                for chara, cid in chara_list:
                    if chara is None:
                        Spacer(gw, gh)
                        continue
                    rank = character_rank[cid]
                    with Frame().set_size((gw, gh)):
                        chara_img = await get_img_from_path(ASSETS_BASE_DIR, f"{RESULT_ASSET_PATH}/chara_rank_icon/{chara}.png")
                        ImageBox(chara_img, size=(gw, gh), use_alpha_blend=True)
                        t = TextBox(str(rank), TextStyle(font=DEFAULT_FONT, size=20, color=(40, 40, 40, 255)))
                        t.set_size((60, 48)).set_content_align('c').set_offset((36, 4))
            
            # 挑战Live等级
            if solo_live is not None:
                chara_id2nickname = {
                    21:"miku", 22:"rin", 23: "len", 24: "luka", 25: "meiko", 26: "kaito", 
                    1: "ick", 2: "saki", 3: "hnm", 4: "shiho", 
                    5: "mnr", 6: "hrk", 7: "airi", 8: "szk",
                    9: "khn", 10: "an", 11: "akt", 12: "toya",
                    13: "tks", 14: "emu", 15: "nene", 16: "rui",
                    17: "knd", 18: "mfy", 19: "ena", 20: "mzk",
                }
                with VSplit().set_content_align('c').set_item_align('c').set_padding((32, 64)).set_sep(12):
                    t = TextBox(f"CHANLLENGE LIVE", TextStyle(font=DEFAULT_FONT, size=18, color=(50, 50, 50, 255)))
                    t.set_bg(roundrect_bg(radius=6)).set_padding((10, 7))
                    with Frame():
                        chara_img = await get_img_from_path(ASSETS_BASE_DIR, f"{RESULT_ASSET_PATH}/chara_rank_icon/{chara_id2nickname[solo_live.character_id]}.png")
                        ImageBox(chara_img, size=(100, 50), use_alpha_blend=True)
                        t = TextBox(str(solo_live.rank), TextStyle(font=DEFAULT_FONT, size=22, color=(40, 40, 40, 255)), overflow='clip')
                        t.set_size((50, 50)).set_content_align('c').set_offset((40, 5))
                    t = TextBox(f"SCORE {solo_live.score}", TextStyle(font=DEFAULT_FONT, size=18, color=(50, 50, 50, 255)))
                    t.set_bg(roundrect_bg(radius=6)).set_padding((10, 7))
        return ret

    vertical = bg_settings.vertical

    with Canvas(bg=bg).set_padding(BG_PADDING) as canvas:
        if not vertical:
            with HSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
                await draw_info()
                with VSplit().set_content_align('c').set_item_align('c').set_sep(16):
                    await draw_play()
                    await draw_chara()
        else:
            with VSplit().set_content_align('c').set_item_align('c').set_sep(16).set_item_bg(ui_bg):
                (await draw_info()).set_bg(None)
                (await draw_play()).set_bg(None)
                (await draw_chara()).set_bg(None)

    if rqd.update_time:
        update_time = datetime.fromtimestamp(rqd.update_time / 1000).strftime('%Y-%m-%d %H:%M:%S')
    else:
        update_time = "?"
    text = f"DT: {update_time}  " + DEFAULT_WATERMARK
    if bg_settings.img_path:
        text = text + f"  This background is user-uploaded."
    add_watermark(canvas, text)
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
    with Frame().set_bg(roundrect_bg(alpha=80)).set_padding(16) as f:
        with HSplit().set_content_align('c').set_item_align('c').set_sep(14):
            # 个人信息
            if rqd.profile:
                # 框
                frame_path = rqd.profile.frame_path
                has_frame = rqd.profile.has_frame
                avatar_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.profile.leader_image_path)
                # 头像
                avatar_widget = await get_avatar_widget_with_frame(
                    is_frame=bool(has_frame),
                    frame_path=frame_path,
                    avatar_img=avatar_img,
                    avatar_w=80,
                    frame_data=[]
                )
                with VSplit().set_content_align('c').set_item_align('l').set_sep(5):
                    # 昵称、id和区服
                    colored_text_box(
                        truncate(rqd.profile.nickname, 64),
                        TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK, use_shadow=True, shadow_offset=2),
                    )                    
                    user_id = process_hide_uid(rqd.profile.is_hide_uid, rqd.profile.id, keep=6)
                    TextBox(f"{rqd.profile.region.upper()}: {user_id}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
                    # 数据源信息
                    for data_source in rqd.data_sources:
                        # 数据名称
                        source_text = data_source.name
                        if data_source.source:
                            source_text += f" 数据来源: {data_source.source}"
                        if data_source.mode:
                            source_text += f" 获取模式: {data_source.mode}"
                        TextBox(f"{source_text}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
                        # 数据更新时间
                        if data_source.update_time:
                            update_time = datetime.fromtimestamp(data_source.update_time / 1000)
                            update_time_text = update_time.strftime('%m-%d %H:%M:%S') + f" ({get_readable_datetime(update_time, show_original_time=False)})"
                            TextBox(f"更新时间: {update_time_text}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
            # 错误/警告
            if rqd.error_message:
                TextBox(rqd.error_message, TextStyle(font=DEFAULT_FONT, size=20, color=RED), line_count=3).set_w(240)
