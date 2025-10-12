from datetime import datetime
from typing import Any, List, Dict

from PIL import Image
from pydantic import BaseModel

from src.base.configs import ASSETS_BASE_DIR
from src.base.draw import (
    BG_PADDING,
    DIFF_COLORS,
    SEKAI_BLUE_BG,
    add_watermark,
    roundrect_bg,
)
from src.base.painter import (
    DEFAULT_BOLD_FONT,
    DEFAULT_FONT,
    DEFAULT_HEAVY_FONT,
    WHITE,
    LinearGradient,
    adjust_color,
    lerp_color,
)
from src.base.plot import Canvas, FillBg, Frame, Grid, HSplit, ImageBox, Spacer, TextBox, TextStyle, VSplit
from src.base.utils import get_img_from_path, get_readable_timedelta, get_str_display_length


class MusicMD(BaseModel):
    id: int
    title: str
    composer: str
    lyricist: str
    arranger: str
    mv: list[str] | None = None
    categories: list[str]
    publishedAt: int
    isFullLength: bool

class DifficultyInfo(BaseModel):
    level: list[int]
    note_count: list[int]
    has_append: bool

class MusicVocalInfo(BaseModel):
    vocal_info: dict[str, Any] # {"caption": str, "characters": [{"characterName": str}]}
    vocal_assets: dict[str, str] # {"xxx": path}

class UserProfileInfo(BaseModel):
    uid: str
    region: str
    nickname: str
    data_source: str
    update_time: int
    music_results: list[dict[str, Any]] # {"musicId": int, "musicDifficultyType": str, "musicDifficulty": str, "playResult": str}

class MusicDetailRequest(BaseModel):
    region: str
    music_info: MusicMD
    bpm: int | None = None
    vocal: MusicVocalInfo
    alias: list[str] | None
    length: str | None = None
    difficulty: DifficultyInfo
    eventId: int | None = None
    cn_name: str | None = None
    music_jacket: str
    event_banner: str | None = None

class MusicBriefList(BaseModel):
    difficulty: DifficultyInfo
    music_info: MusicMD
    music_jacket: str

class MusicBriefListRequest(BaseModel):
    music_list: list[MusicBriefList]
    region: str

class MusicListRequest(BaseModel):
    user_info: UserProfileInfo
    music_list: List[Dict[str, Any]] # [{"id": int, "difficulty": str}]
    jackets: Dict[str, str] # {musicId: jacket_path}

async def compose_music_detail_image(rqd: MusicDetailRequest,title: str=None, title_style: TextStyle=None, title_shadow=False):
    # 数据准备
    mid = rqd.music_info.id
    name = rqd.music_info.title
    composer = rqd.music_info.composer
    lyricist = rqd.music_info.lyricist
    arranger = rqd.music_info.arranger
    mv_info = rqd.music_info.mv
    publish_time = datetime.fromtimestamp(rqd.music_info.publishedAt / 1000).strftime("%Y-%m-%d %H:%M:%S")
    bpm = rqd.bpm
    is_full_length = rqd.music_info.isFullLength
    cover_img = get_img_from_path(ASSETS_BASE_DIR,rqd.music_jacket)
    length = rqd.length
    cn_name = rqd.cn_name
    region = rqd.region
    vocal_info = rqd.vocal.vocal_info
    vocal_logos_raw = rqd.vocal.vocal_assets
    caption_vocals = {}
    has_append = rqd.difficulty.has_append
    event_banner = get_img_from_path(ASSETS_BASE_DIR,rqd.event_banner)

    if not has_append:
        DIFF_COLORS.pop("append")

    vocal_logos = {}
    for char_name, logo_path in vocal_logos_raw.items():
        img = get_img_from_path(ASSETS_BASE_DIR,logo_path)
        if img:
            vocal_logos[char_name] = img

    if is_full_length:
        name += " [FULL]"

    audio_len = length
    bpm_main = f"{bpm} BPM" if bpm else "?"

    diff_lvs    = rqd.difficulty.level
    diff_counts = rqd.difficulty.note_count
    has_append  = rqd.difficulty.has_append

    event_id = rqd.eventId

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
                            if diff_lvs[i] is not None:
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
                publish_time = datetime.fromtimestamp(m.music_info.publishedAt / 1000)
                publish_dlt = get_readable_timedelta(publish_time - datetime.now(), precision="d")
                diffs = ["easy", "normal", "hard", "expert", "master", "append"]
                diff_lvs = m.difficulty.level

                style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(50, 50, 50))
                style2 = TextStyle(font=DEFAULT_FONT, size=16, color=(70, 70, 70))
                style3 = TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=WHITE)

                with HSplit().set_content_align("c").set_item_align("c").set_sep(8).set_padding(16):
                    ImageBox(get_img_from_path(ASSETS_BASE_DIR,m.music_jacket), size=(80, 80))
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
    lv_musics = rqd.music_list
    for i in range(len(lv_musics)):
        id, lv = lv_musics[i]
        covers = rqd.jackets[id]
        for m, cover in zip(id, covers):
            m["cover_img"] = cover

    profile = rqd.user_info

    if play_result_filter is None:
        play_result_filter = ["clear", "not_clear", "fc", "ap"]

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16) as vs:
            if profile:
                await get_detailed_profile_card(ctx, profile, err_msg)

            with VSplit().set_bg(roundrect_bg()).set_padding(16).set_sep(16):
                lv_musics.sort(key=lambda x: x[0], reverse=False)
                for lv, musics in lv_musics:
                    musics.sort(key=lambda x: x["publishedAt"], reverse=False)

                    # 获取游玩结果并过滤
                    filtered_musics = []
                    for music in musics:
                        # 过滤剧透
                        is_leak = datetime.fromtimestamp(music["publishedAt"] / 1000) > datetime.now()
                        music["is_leak"] = is_leak
                        if is_leak and not show_leak:
                            continue
                        # 获取游玩结果
                        result_type = None
                        if profile:
                            mid = music["id"]
                            results = find_by(profile["userMusicResults"], "musicId", mid, mode="all")
                            results = find_by(results, "musicDifficultyType", diff, mode="all") + find_by(results, "musicDifficulty", diff, mode="all")
                            if results:
                                has_clear, full_combo, all_prefect = False, False, False
                                for item in results:
                                    has_clear = has_clear or item["playResult"] != "not_clear"
                                    full_combo = full_combo or item["fullComboFlg"]
                                    all_prefect = all_prefect or item["fullPerfectFlg"]
                                result_type = "clear" if has_clear else "not_clear"
                                if full_combo: result_type = "fc"
                                if all_prefect: result_type = "ap"
                            # 过滤游玩结果(无结果视为not_clear)
                            if (result_type or "not_clear") not in play_result_filter:
                                continue
                        music["play_result"] = result_type
                        filtered_musics.append(music)

                    if not filtered_musics: continue

                    with VSplit().set_bg(roundrect_bg()).set_padding(8).set_item_align("lt").set_sep(8):
                        lv_text = TextBox(f"{diff.upper()} {lv}", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=WHITE))
                        lv_text.set_padding((10, 5)).set_bg(roundrect_bg(fill=DIFF_COLORS[diff], radius=5))

                        with Grid(col_count=10).set_sep(5):
                            for music in filtered_musics:
                                with VSplit().set_sep(2):
                                    with Frame():
                                        ImageBox(music["cover_img"], size=(64, 64), image_size_mode="fill")
                                        if music["is_leak"]:
                                            TextBox("LEAK", TextStyle(font=DEFAULT_BOLD_FONT, size=12, color=RED)) \
                                                .set_bg(roundrect_bg(radius=4)).set_offset((64, 64)).set_offset_anchor("rb")
                                        if music["play_result"]:
                                            result_img = ctx.static_imgs.get(f"icon_{music['play_result']}.png")
                                            ImageBox(result_img, size=(16, 16), image_size_mode="fill").set_offset((64 - 10, 64 - 10))
                                    if show_id:
                                        TextBox(f"{music['id']}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK)).set_w(64)

    add_watermark(canvas)
    return await canvas.get_img()
