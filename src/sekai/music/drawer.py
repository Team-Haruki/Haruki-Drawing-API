from datetime import datetime
import asyncio
from PIL import Image

from src.sekai.profile.drawer import get_detailed_profile_card, get_profile_card
from src.sekai.base.configs import ASSETS_BASE_DIR, RESULT_ASSET_PATH
from src.sekai.base.draw import (
    BG_PADDING,
    DIFF_COLORS,
    SEKAI_BLUE_BG,
    add_watermark,
    roundrect_bg,
    PLAY_RESULT_COLORS,
)
from src.sekai.base.painter import (
    DEFAULT_BOLD_FONT,
    DEFAULT_FONT,
    DEFAULT_HEAVY_FONT,
    WHITE,
    LinearGradient,
    adjust_color,
    lerp_color,
    BLACK,
    RED
)
from src.sekai.base.plot import (
    Canvas,
    FillBg,
    Frame,
    Grid,
    HSplit,
    ImageBox,
    Spacer,
    TextBox,
    TextStyle,
    VSplit
)
from src.sekai.base.utils import get_img_from_path, get_readable_timedelta, get_str_display_length


# =========================== 从.model导入常量和数据类型 =========================== #

from .model import *

# =========================== 绘图函数 =========================== #

async def compose_music_detail_image(rqd: MusicDetailRequest,title: str=None, title_style: TextStyle=None, title_shadow=False):
    # 数据准备
    mid = rqd.music_info.id
    name = rqd.music_info.title
    composer = rqd.music_info.composer
    lyricist = rqd.music_info.lyricist
    arranger = rqd.music_info.arranger
    mv_info = rqd.music_info.mv_info
    publish_time = datetime.fromtimestamp(rqd.music_info.release_at / 1000).strftime("%Y-%m-%d %H:%M:%S")
    bpm = rqd.bpm
    is_full_length = rqd.music_info.is_full_length
    cover_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.music_jacket_path)
    length = rqd.length
    cn_name = rqd.cn_name
    region = rqd.region
    vocal_info = rqd.vocal.vocal_info
    vocal_logos_raw = rqd.vocal.vocal_assets
    caption_vocals = {}
    # has_append = rqd.difficulty.has_append
    event_banner = await get_img_from_path(ASSETS_BASE_DIR, rqd.event_banner_path) if rqd.event_banner_path else None

    # if not has_append:
    #     DIFF_COLORS.pop("append")

    vocal_logos = {}
    for char_name, logo_path in vocal_logos_raw.items():
        img = await get_img_from_path(ASSETS_BASE_DIR,logo_path)
        if img:
            vocal_logos[char_name] = img

    if is_full_length:
        name += " [FULL]"

    audio_len = length
    bpm_main = f"{bpm} BPM" if bpm else "?"

    diff_lvs    = rqd.difficulty.level
    diff_counts = rqd.difficulty.note_count
    has_append  = rqd.difficulty.has_append

    event_id = rqd.event_id

    caption_vocals = {}
    if vocal_info and "caption" in vocal_info and "characters" in vocal_info:
        caption = vocal_info.get("caption", "Vocal").replace("ver.", "")
        if caption not in caption_vocals:
            caption_vocals[caption] = []

        vocal_group = {"chara_imgs": [], "vocal_names": []}
        for chara_data in vocal_info["characters"]:
            chara_name = chara_data.get("characterName")
            if chara_name in vocal_logos:
                vocal_group["chara_imgs"].append(vocal_logos[chara_name])
            elif chara_name:
                vocal_group["vocal_names"].append(chara_name)
        caption_vocals[caption].append(vocal_group)


    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16).set_item_bg(roundrect_bg(alpha=80)):
            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_padding(16).set_item_bg(roundrect_bg(alpha=80)):
                # 附加标题
                if title and title_style:
                    if title_shadow:
                        TextBox(title,TextStyle(title_style.font, title_style.size, title_style.color, use_shadow=True, shadow_offset=2),).set_padding(16).set_omit_parent_bg(True).set_bg(roundrect_bg())
                    else:
                        TextBox(title, title_style).set_padding(16).set_omit_parent_bg(True).set_bg(roundrect_bg())

                # 歌曲标题
                name_text = f"【{region.upper()}-{mid}】{name}"
                if cn_name: name_text += f"  ({cn_name})"
                TextBox(name_text, TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(20, 20, 20)), use_real_line_count=True).set_padding(16).set_w(800)

                with HSplit().set_content_align("c").set_item_align("c").set_sep(16):
                    # 封面
                    with Frame().set_padding(32):
                        Spacer(w=300, h=300).set_bg(FillBg((0, 0, 0, 100))).set_offset((4, 4))
                        ImageBox(cover_img, size=(None, 300))
                    # 信息
                    style1 = TextStyle(font=DEFAULT_HEAVY_FONT, size=30, color=(50, 50, 50))
                    style2 = TextStyle(font=DEFAULT_FONT, size=30, color=(70, 70, 70))
                    with HSplit().set_padding(16).set_sep(32).set_content_align("c").set_item_align("c"):
                        with VSplit().set_content_align("c").set_item_align("c").set_sep(8).set_padding(0):
                            TextBox("作曲", style1)
                            TextBox("作词", style1)
                            TextBox("编曲", style1)
                            TextBox("MV", style1)
                            TextBox("时长", style1)
                            TextBox("发布时间", style1)
                            TextBox("BPM", style1)

                        with VSplit().set_content_align("c").set_item_align("c").set_sep(8).set_padding(0):
                            TextBox(composer, style2)
                            TextBox(lyricist, style2)
                            TextBox(arranger, style2)
                            mv_text = ""
                            if mv_info:
                                for item in mv_info:
                                    if item == "original": mv_text += "原版MV & "
                                    if item == "mv": mv_text += "3DMV & "
                                    if item == "mv_2d": mv_text += "2DMV & "
                            mv_text = mv_text[:-3]
                            if not mv_text: mv_text = "无"
                            TextBox(mv_text, style2)
                            TextBox(audio_len, style2)
                            TextBox(publish_time, style2)
                            TextBox(bpm_main, style2)

                # 难度等级/物量
                hs, vs, gw = 8, 12, 180 if not has_append else 150
                with HSplit().set_content_align("c").set_item_align("c").set_sep(vs).set_padding(32):
                    with Grid(col_count=(6 if has_append else 5), item_size_mode="fixed").set_sep(h_sep=hs, v_sep=vs):
                        # 难度等级
                        light_diff_color = []
                        for i, (diff, color) in enumerate(DIFF_COLORS.items()):
                            if i < len(diff_lvs) and diff_lvs[i] is not None:
                                t = TextBox(f"{diff.upper()} {diff_lvs[i]}", TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=WHITE))
                                t.set_bg(roundrect_bg(fill=color, radius=6)).set_size((gw, 40)).set_content_align("c").set_overflow("clip")
                            if not isinstance(color, LinearGradient):
                                light_diff_color.append(adjust_color(lerp_color(color, WHITE, 0.5), a=100))
                            else:
                                light_diff_color.append(adjust_color(lerp_color(color.c2, WHITE, 0.5), a=100))
                                # 物量
                        for i, count in enumerate(diff_counts):
                            if count is None: continue
                            t = TextBox(f"{count} combo", TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(80, 80, 80, 255)), line_count=1)
                            t.set_size((gw, 40)).set_content_align("c").set_bg(roundrect_bg(fill=light_diff_color[i], radius=6))

                            # 别名
                aliases = rqd.alias
                if aliases:
                    alias_text = "，". join(aliases)
                    font_size = max(10, 24 - get_str_display_length(alias_text) // 40 * 1)
                    with HSplit().set_content_align("l").set_item_align("l").set_sep(16).set_padding(16):
                        TextBox("歌曲别名", TextStyle(font=DEFAULT_HEAVY_FONT, size=24, color=(50, 50, 50)))
                        aw = 800
                        TextBox(alias_text, TextStyle(font=DEFAULT_FONT, size=font_size, color=(70, 70, 70)), use_real_line_count=True).set_w(aw)

                def draw_vocal():
                    # 歌手
                    with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_padding(16):
                        for caption, vocals in sorted(caption_vocals.items(), key=lambda x: len(x[1])):
                            with HSplit().set_padding(0).set_sep(4).set_content_align("c").set_item_align("c"):
                                TextBox(caption + "  ver.", TextStyle(font=DEFAULT_HEAVY_FONT, size=24, color=(50, 50, 50)))
                                Spacer(w=16)
                                for vocal in vocals:
                                    with HSplit().set_content_align("c").set_item_align("c").set_sep(4).set_padding(4).set_bg(roundrect_bg(fill=(255, 255, 255, 150), radius=8)):
                                        if vocal["vocal_names"]:
                                            TextBox(vocal["vocal_names"], TextStyle(font=DEFAULT_FONT, size=24, color=(70, 70, 70)))
                                        else:
                                            for img in vocal["chara_imgs"]:
                                                ImageBox(img, size=(32, 32), use_alpha_blend=True)
                                    Spacer(w=8)
                def draw_event():
                    # 活动
                    with HSplit().set_sep(8):
                        with VSplit().set_content_align("c").set_item_align("c").set_sep(8).set_padding(16):
                            TextBox("关联活动", TextStyle(font=DEFAULT_HEAVY_FONT, size=24, color=(50, 50, 50)))
                            TextBox(f"ID: {event_id}", TextStyle(font=DEFAULT_FONT, size=24, color=(70, 70, 70)))
                        ImageBox(event_banner, size=(None, 100)).set_padding(16)

                if event_id is not None:
                    with HSplit().set_omit_parent_bg(True).set_item_bg(roundrect_bg(alpha=80)).set_padding(0).set_sep(16):
                        draw_vocal()
                        draw_event()
                else:
                    draw_vocal()

    add_watermark(canvas)
    return await canvas.get_img()

async def compose_music_brief_list_image(rqd: MusicBriefListRequest,title: str = None,title_style: TextStyle = None,
    title_shadow=False,) -> Image.Image:

    musics_list = rqd.music_list
    max_num = 50
    hide_num = max(0, len(musics_list) - max_num)
    musics_list = musics_list[:max_num]
    region = rqd.region

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_item_bg(roundrect_bg(alpha=80)):
            if title and title_style:
                if title_shadow:
                    TextBox(
                        title,
                        TextStyle(
                            title_style.font, title_style.size, title_style.color, use_shadow=True, shadow_offset=2
                        ),
                    ).set_padding(8)
                else:
                    TextBox(title, title_style).set_padding(8)

            for m in musics_list:
                mid, music_name = m.music_info.id, m.music_info.title
                publish_time = datetime.fromtimestamp(m.music_info.release_at / 1000)
                publish_dlt = get_readable_timedelta(publish_time - datetime.now(), precision="d")
                diffs = ["easy", "normal", "hard", "expert", "master", "append"]
                diff_lvs = m.difficulty.level

                style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(50, 50, 50))
                style2 = TextStyle(font=DEFAULT_FONT, size=16, color=(70, 70, 70))
                style3 = TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=WHITE)

                with HSplit().set_content_align("c").set_item_align("c").set_sep(8).set_padding(16):
                    ImageBox(await get_img_from_path(ASSETS_BASE_DIR, m.music_jacket_path), size=(80, 80))
                    with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8):
                        TextBox(f"【{region.upper()}-{mid}】{music_name}", style1).set_w(250)
                        if publish_dlt <= "0秒":
                            TextBox(f"  {publish_time.strftime('%Y-%m-%d %H:%M:%S')}", style2)
                        else:
                            TextBox(f"  {publish_time.strftime('%Y-%m-%d %H:%M:%S')} ({publish_dlt}后)", style2)
                        with HSplit().set_content_align("c").set_item_align("c").set_sep(4):
                            Spacer(w=2)
                            for diff, level in zip(diffs, diff_lvs):
                                if level is not None:
                                    TextBox(str(level), style3, overflow="clip").set_bg(
                                        roundrect_bg(fill=DIFF_COLORS[diff], radius=8)
                                    ).set_content_align("c").set_size((28, 28))

            if hide_num:
                TextBox(
                    f"{hide_num}首歌曲未显示", TextStyle(font=DEFAULT_FONT, size=20, color=(20, 20, 20))
                ).set_padding(8)

    add_watermark(canvas)
    return await canvas.get_img()

async def compose_music_list_image(
        rqd: MusicListRequest, show_id: bool, show_leak: bool, play_result_filter: list[str] = None,
) -> Image.Image:
    jackets = {}
    jacket_tasks = [get_img_from_path(ASSETS_BASE_DIR, path) for path in rqd.jackets_path_list.values()]
    loaded_jackets = await asyncio.gather(*jacket_tasks)
    for music_id, img in zip(rqd.jackets_path_list.keys(), loaded_jackets):
        jackets[music_id] = img

    profile = rqd.profile
    if play_result_filter is None:
        play_result_filter = ["clear", "not_clear", "fc", "ap"]
    lv_musics_map = {}
    for music in rqd.music_list:
        lv = music["difficulty"]
        if lv not in lv_musics_map:
            lv_musics_map[lv] = []
        lv_musics_map[lv].append(music)
    lv_musics = sorted(lv_musics_map.items(), key=lambda x: x[0], reverse=False)

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16) as vs:
            if profile:
                await get_detailed_profile_card(profile)

            with VSplit().set_bg(roundrect_bg(alpha=80)).set_padding(16).set_sep(16):
                lv_musics.sort(key=lambda x: x[0], reverse=False)
                for lv, musics in lv_musics:
                    musics.sort(key=lambda x: x["release_at"], reverse=False)

                    # 获取游玩结果并过滤
                    filtered_musics = []
                    for music in musics:
                        # 过滤剧透
                        is_leak = datetime.fromtimestamp(music["release_at"] / 1000) > datetime.now()
                        music["is_leak"] = is_leak
                        if is_leak and not show_leak:
                            continue
                        # 获取游玩结果
                        result_type = rqd.user_results[music["id"]]
                        if (result_type or "not_clear") not in play_result_filter:
                            continue
                        music["play_result"] = result_type
                        filtered_musics.append(music)

                    if not filtered_musics: continue

                    diff = rqd.required_difficulties
                    with VSplit().set_bg(roundrect_bg(alpha=80)).set_padding(8).set_item_align("lt").set_sep(8):
                        lv_text = TextBox(f"{diff.upper()} {lv}", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=WHITE))
                        lv_text.set_padding((10, 5)).set_bg(roundrect_bg(fill=DIFF_COLORS[diff], radius=5))

                        with Grid(col_count=10).set_sep(5):
                            for music in filtered_musics:
                                with VSplit().set_sep(2):
                                    with Frame():
                                        ImageBox(jackets[music["id"]], size=(64, 64), image_size_mode="fill")
                                        if music["is_leak"]:
                                            TextBox("LEAK", TextStyle(font=DEFAULT_BOLD_FONT, size=12, color=RED)) \
                                                .set_bg(roundrect_bg(radius=4)).set_offset((64, 64)).set_offset_anchor("rb")
                                        if music["play_result"]:
                                            if rqd.play_result_icon_path_map and music['play_result'] in rqd.play_result_icon_path_map:
                                                result_img_path = rqd.play_result_icon_path_map[music['play_result']]
                                            else:
                                                result_img_path = RESULT_ASSET_PATH+f"/icon_{music['play_result']}.png"
                                            result_img = await get_img_from_path(ASSETS_BASE_DIR, result_img_path)
                                            ImageBox(result_img, size=(16, 16), image_size_mode="fill").set_offset((64 - 10, 64 - 10))
                                    if show_id:
                                        TextBox(f"{music['id']}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK)).set_w(64)

    add_watermark(canvas)
    return await canvas.get_img()

async def compose_play_progress_image(
    rqd: PlayProgressRequest
) -> Image.Image:
    r"""compose_play_progress_image

    合成打歌进度图片
    
    TODO:
        TextBox shadow 暂未实现
    """
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
            if rqd.profile:
                await get_profile_card(rqd.profile)

            bar_h, item_h, w = 200, 48, 48
            font_sz = 24

            with HSplit().set_content_align('c').set_item_align('c').set_bg(roundrect_bg(alpha=80)) \
                .set_padding(64).set_sep(8):

                async def draw_icon(path):
                    path = await get_img_from_path(ASSETS_BASE_DIR, RESULT_ASSET_PATH+f"/{path}")
                    with Frame().set_size((w, item_h)).set_content_align('c'):
                        ImageBox(path, size=(w // 2, w // 2))
                
                # 第一列：进度条的占位 难度占位 not_clear clear fc ap 图标
                with VSplit().set_content_align('c').set_item_align('c').set_sep(8):
                    Spacer(w=w, h=bar_h)
                    Spacer(w=w, h=item_h)
                    await draw_icon("icon_not_clear.png")
                    await draw_icon("icon_clear.png")
                    await draw_icon("icon_fc.png")
                    await draw_icon("icon_ap.png")

                # 之后的几列：进度条 难度 各个类型的数量
                for c in rqd.counts:
                    with VSplit().set_content_align('c').set_item_align('c').set_sep(8):
                        # 进度条
                        def draw_bar(color, h, blur_glass=False):
                            return Frame().set_size((w, h)).set_bg(roundrect_bg(fill=color, radius=4, blur_glass=blur_glass))
                        with draw_bar(PLAY_RESULT_COLORS['not_clear'], bar_h, blur_glass=True).set_content_align('b') as f:
                            if c.clear: draw_bar(PLAY_RESULT_COLORS['clear'], int(bar_h * c.clear / c.total))
                            if c.fc:    draw_bar(PLAY_RESULT_COLORS['fc'],    int(bar_h * c.fc / c.total))
                            if c.ap:    draw_bar(PLAY_RESULT_COLORS['ap'],    int(bar_h * c.ap / c.total))

                        # 难度
                        TextBox(f"{c.level}", TextStyle(font=DEFAULT_BOLD_FONT, size=font_sz, color=WHITE), overflow='clip') \
                            .set_bg(roundrect_bg(fill=DIFF_COLORS[rqd.difficulty], radius=16)) \
                            .set_size((w, item_h)).set_content_align('c')
                        # 数量 (第一行虽然图标是not_clear但是实际上是total)
                        color = PLAY_RESULT_COLORS['not_clear']
                        ap      = c.ap
                        fc      = c.fc - c.ap
                        clear   = c.clear - c.fc
                        total   = c.total - c.clear
                        style = TextStyle(DEFAULT_BOLD_FONT, font_sz, color, use_shadow=False)
                        TextBox(f"{total}", style, overflow='clip').set_size((w, item_h)).set_content_align('c').set_bg(roundrect_bg(alpha=80))
                        style = TextStyle(DEFAULT_BOLD_FONT, font_sz, color, use_shadow=True, shadow_color=PLAY_RESULT_COLORS['clear'], shadow_offset=2)
                        TextBox(f"{clear}", style, overflow='clip').set_size((w, item_h)).set_content_align('c').set_bg(roundrect_bg(alpha=80))
                        style = TextStyle(DEFAULT_BOLD_FONT, font_sz, color, use_shadow=True, shadow_color=PLAY_RESULT_COLORS['fc'], shadow_offset=2)
                        TextBox(f"{fc}",    style, overflow='clip').set_size((w, item_h)).set_content_align('c').set_bg(roundrect_bg(alpha=80))
                        style = TextStyle(DEFAULT_BOLD_FONT, font_sz, color, use_shadow=True, shadow_color=PLAY_RESULT_COLORS['ap'], shadow_offset=2)
                        TextBox(f"{ap}",    style, overflow='clip').set_size((w, item_h)).set_content_align('c').set_bg(roundrect_bg(alpha=80))

    add_watermark(canvas)
    return await canvas.get_img()


def draw_text_icon(
    text: str, 
    icon: Image.Image, 
    style: TextStyle
) -> HSplit:
    r"""draw_text_icon

    绘制文字和图标，
    只在合成歌曲奖励图片的两个函数中使用

    Args
    ----
    text : str
        要绘制的文字
    icon : Image.Image
        要绘制的图标
    style : TextStyle
        绘制的文字样式
    
    Return
    ------
    HSplit
    """
    with HSplit().set_content_align('c').set_item_align('c').set_sep(4) as hs:
        if text is not None:
            TextBox(str(text), style, overflow='clip')
        ImageBox(icon, size=(None, 40))
    return hs



async def compose_detail_music_rewards_image(
    rqd: DetailMusicRewardsRequest
) -> Image.Image:
    r"""compose_detail_music_rewards_image

    在有抓包数据的情况下合成歌曲奖励图片

    Args
    ----
    rqd : DetailMusicRewardsRequest
        在有抓包数据的情况下合成歌曲奖励图片所必需的数据
    
    Return
    ------
    PIL.Image.Image
    """
    # 网格宽度和高度
    gw, gh = 80, 40
    # 样式
    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50)) 
    style2 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(75, 75, 75)) 
    # 奖励的icon
    j_path = rqd.jewel_icon_path or RESULT_ASSET_PATH+"/jewel.png"
    s_path = rqd.shard_icon_path or RESULT_ASSET_PATH+"/shard.png"
    jewel_icon: Image.Image = await get_img_from_path(ASSETS_BASE_DIR, j_path)
    shard_icon: Image.Image = await get_img_from_path(ASSETS_BASE_DIR, s_path)
    
    # 绘图
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
            await get_profile_card(rqd.profile)
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_padding(16).set_bg(roundrect_bg(alpha=80)):
                # 乐曲评级奖励
                with HSplit().set_content_align('lt').set_item_align('lt').set_sep(24).set_padding(16).set_bg(roundrect_bg(alpha=80)):
                    TextBox("歌曲评级奖励(S)", style1). \
                        set_size((None, gh)). \
                        set_content_align('c')
                    draw_text_icon(rqd.rank_rewards, jewel_icon, style2). \
                        set_size((None, gh))
                # 连击奖励
                with HSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_item_bg(roundrect_bg(alpha=80)):
                    for diff in ('hard', 'expert', 'master', 'append'): # 因为go的map是无序的，用这个保证顺序
                        with HSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(16):
                            # 难度
                            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8):
                                Spacer(w=gw, h=gh)
                                for combo_reward in rqd.combo_rewards[diff]: # slice是有序的，所以不用再排序
                                    TextBox(str(combo_reward.level), TextStyle(DEFAULT_BOLD_FONT, 24, WHITE), overflow='clip'). \
                                        set_size((gh, gh)). \
                                        set_content_align('c').set_bg(roundrect_bg(fill=DIFF_COLORS[diff], radius=8))
                            # 奖励
                            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8):
                                ImageBox(jewel_icon if diff != 'append' else shard_icon, size=(None, gh))
                                for combo_reward in rqd.combo_rewards[diff]:
                                    TextBox(str(combo_reward.reward), style2, overflow='clip'). \
                                        set_size((gw, gh)). \
                                        set_content_align('l')
                            # 累计奖励
                            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8):
                                TextBox("累计", style1). \
                                    set_size((gw, gh)). \
                                    set_content_align('l') 
                                acc = 0
                                for combo_reward in rqd.combo_rewards[diff]:
                                    acc += combo_reward.reward
                                    TextBox(str(acc), style2, overflow='clip'). \
                                        set_size((gw, gh)). \
                                        set_content_align('l')

    add_watermark(canvas)
    return await canvas.get_img()

async def compose_basic_music_rewards_image(
    rqd: BasicMusicRewardsRequest
) -> Image.Image:
    r"""compose_basic_music_rewards_image

    在仅基础数据的情况下合成歌曲奖励图片

    Args
    ----
    rqd : BasicMusicRewardsRequest
        在仅基础数据的情况下合成歌曲奖励图片所必需的数据
    
    Return
    ------
    PIL.Image.Image
    """
    # 网格宽度和高度
    gw, gh = 80, 40
    # 样式
    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50)) 
    style2 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(75, 75, 75)) 
    # 奖励的icon
    j_path = rqd.jewel_icon_path or f"{RESULT_ASSET_PATH}/jewel.png"
    s_path = rqd.shard_icon_path or f"{RESULT_ASSET_PATH}/shard.png"
    jewel_icon: Image.Image = await get_img_from_path(ASSETS_BASE_DIR, j_path)
    shard_icon: Image.Image = await get_img_from_path(ASSETS_BASE_DIR, s_path)
    # 绘图
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
            await get_profile_card(rqd.profile)
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_padding(16).set_bg(roundrect_bg(alpha=80)):
                # 说明
                TextBox("仅显示简略估计数据（假设Clear的歌曲都是S评级，未FC的歌曲都没拿到连击奖励）",
                        TextStyle(DEFAULT_FONT, 20, (200, 75, 75)), use_real_line_count=True).set_w(480)
                # 乐曲评级奖励
                with HSplit().set_content_align('lt').set_item_align('lt').set_sep(24).set_padding(16).set_bg(roundrect_bg(alpha=80)):
                    TextBox("歌曲评级奖励(S)", style1). \
                        set_size((None, gh)). \
                        set_content_align('c')
                    draw_text_icon(rqd.rank_rewards, jewel_icon, style2). \
                        set_size((None, gh))
                # 连击奖励
                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(16).set_bg(roundrect_bg(alpha=80)):
                    for diff in ['hard', 'expert', 'master', 'append']:
                        with HSplit().set_content_align('lt').set_item_align('lt').set_sep(24):
                            TextBox(f"{diff.upper()}", TextStyle(DEFAULT_BOLD_FONT, 24, WHITE), overflow='clip'). \
                                set_bg(roundrect_bg(fill=DIFF_COLORS[diff], radius=8)). \
                                set_size((120, gh)).set_content_align('c')
                            TextBox("连击奖励", style1). \
                                set_size((None, gh)). \
                                set_content_align('l')
                            draw_text_icon(rqd.combo_rewards[diff], jewel_icon if diff != 'append' else shard_icon, style2) \
                                .set_size((None, gh))

    add_watermark(canvas)
    return await canvas.get_img()
