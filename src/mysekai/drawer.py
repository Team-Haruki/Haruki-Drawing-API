from PIL import Image, ImageDraw
from datetime import datetime
from src.base.configs import (
    ASSETS_BASE_DIR,
    DEFAULT_FONT,
    DEFAULT_BOLD_FONT,
    RESULT_ASSET_PATH,
    DEFAULT_HEAVY_FONT
)
from src.base.draw import(
    BG_PADDING,
    roundrect_bg,
    SEKAI_BLUE_BG,
    add_watermark
)
from src.base.utils import (
    get_img_from_path,
    truncate,
    get_readable_datetime,
    get_str_display_length,
    
)
from src.base.plot import(
    Canvas,
    VSplit,
    HSplit,
    Frame,
    colored_text_box,
    ImageBg,
    ImageBox,
    TextStyle,
    TextBox,
    Spacer,
    Grid,
    RoundRectBg
)
from src.base.painter import(
    SHADOW,
    RED,
    BLACK,
    WHITE,
    
)
from src.profile.drawer import (
    get_avatar_widget_with_frame,
    process_hide_uid,
)


from .model import *


# 获取玩家mysekai抓包数据的简单卡片 返回 Frame
async def get_mysekai_info_card(mysekai_info: MysekaiInfoCardRequest, err_msg: str) -> Frame:
    with Frame().set_bg(roundrect_bg(alpha=80)).set_padding(16) as f:
        with HSplit().set_content_align('c').set_item_align('c').set_sep(14):
            if mysekai_info:
                frame_path = mysekai_info.frame_path
                has_frame = mysekai_info.has_frame
                avatar_img = await get_img_from_path(ASSETS_BASE_DIR, mysekai_info.leader_image_path)
                avatar_widget = await get_avatar_widget_with_frame(
                    is_frame=bool(has_frame),
                    frame_path=frame_path,
                    avatar_img=avatar_img,
                    avatar_w=80,
                    frame_data=[]
                )
                with VSplit().set_content_align('c').set_item_align('l').set_sep(5):
                    source = mysekai_info.source                    
                    update_time = datetime.fromtimestamp(mysekai_info.update_time)
                    update_time_text = update_time.strftime('%m-%d %H:%M:%S') + f" ({get_readable_datetime(update_time, show_original_time=False)})"
                    mode = mysekai_info.mode                    
                    user_id = process_hide_uid(mysekai_info.is_hide_uid, mysekai_info.id, keep=6)
                    
                    with HSplit().set_content_align('lb').set_item_align('lb').set_sep(5):
                        hs = colored_text_box(
                            truncate(mysekai_info.nickname, 64),
                            TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK, use_shadow=True, shadow_offset=2, shadow_color=SHADOW), # TODO: shadow_color=ADAPTIVE_SHADOW 自适应颜色暂未实现
                        )
                        name_length = 0
                        for item in hs.items:
                            if isinstance(item, TextBox):
                                name_length += get_str_display_length(item.text)
                        ms_lv = mysekai_info.mysekai_rank
                        ms_lv_text = f"MySekai Lv.{ms_lv}" if name_length <= 12 else f"MSLv.{ms_lv}"
                        TextBox(ms_lv_text, TextStyle(font=DEFAULT_FONT, size=18, color=BLACK))

                    TextBox(f"{mysekai_info.region.upper()}: {user_id} Mysekai数据", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
                    TextBox(f"更新时间: {update_time_text}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
                    TextBox(f"数据来源: {source}  获取模式: {mode}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
            if err_msg:
                TextBox(f"获取数据失败:{err_msg}", TextStyle(font=DEFAULT_FONT, size=20, color=RED), line_count=3).set_w(240)
    return f

# 绘制数量图
async def compose_mysekai_resource_image(
        rqd: MysekaiResourceRequest
) -> Image.Image:
    r"""compose_mysekai_resource_image

    绘制烤森资源数量图

    Args
    ----
    rqd : MysekaiResourceRequest
        绘制烤森资源数量图所必须的数据

    Returns
    -------
    PIL.Image.Image
    """
    # 使用传入的背景图片，如果没有则使用默认蓝色背景
    if rqd.background_image_path:
        try:
            bg_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.background_image_path)
            bg = ImageBg(bg_img)
        except FileNotFoundError:
            bg = SEKAI_BLUE_BG
    else:
        bg = SEKAI_BLUE_BG
    # 基本信息
    mysekai_info = rqd.mysekai_info
    error_message = rqd.error_message
    # 天气
    phenoms = rqd.phenoms
    # 到访角色列表
    visit_characters = rqd.visit_characters
    # 地区资源列表
    site_res_nums = rqd.site_resource_numbers

    with Canvas(bg=bg).set_padding(BG_PADDING).set_content_align('c') as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16) as vs:

            with HSplit().set_sep(28).set_content_align('lb'):
                await get_mysekai_info_card(mysekai_info, error_message)

                # 天气预报
                with HSplit().set_sep(8).set_content_align('lb').set_bg(roundrect_bg(alpha=80)).set_padding(10):
                    for phenom in phenoms:
                        if phenom.refresh_reason == 'natural':
                            phenom_img = await get_img_from_path(ASSETS_BASE_DIR, phenom.image_path)
                        else:
                            bd_status = phenom.refresh_reason.split('_')[0]
                            phenom_img = await get_img_from_path(ASSETS_BASE_DIR, phenom.image_path)    # 露滴道具
                            phenom_img = phenom_img.resize((50, 50), Image.LANCZOS)
                            if bd_status == "bdend": # 结束，在图上画叉
                                draw = ImageDraw.Draw(phenom_img)
                                draw.line((0, 0, phenom_img.width, phenom_img.height), fill=(150, 150, 150, 255), width=5)
                                draw.line((0, phenom_img.height, phenom_img.width, 0), fill=(150, 150, 150, 255), width=5)
                        with Frame():
                            with VSplit().set_content_align('c').set_item_align('c').set_sep(5).set_bg(roundrect_bg(fill=phenom.background_fill)).set_padding(8):
                                TextBox(phenom.text, TextStyle(font=DEFAULT_BOLD_FONT, size=15, color=phenom.text_fill)).set_w(60).set_content_align('c')
                                ImageBox(phenom_img, size=(None, 50), use_alpha_blend=True) 
            
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_padding(16).set_bg(roundrect_bg(alpha=80)):
                # 到访角色列表
                with HSplit().set_bg(roundrect_bg(alpha=80)).set_content_align('c').set_item_align('c').set_padding(16).set_sep(16):
                    gate_id = rqd.gate_id
                    gate_icon = await get_img_from_path(ASSETS_BASE_DIR, f"{RESULT_ASSET_PATH}/mysekai/gate_icon/gate_{gate_id}.png")
                    gate_level = rqd.gate_level
                    with Frame().set_size((64, 64)).set_margin((16, 0)).set_content_align('rb'):
                        ImageBox(gate_icon, size=(64, 64), use_alpha_blend=True, shadow=True).set_offset((0, -4))
                        TextBox(
                            f"Lv.{gate_level}", 
                            TextStyle(DEFAULT_FONT, 16, UNIT_COLORS[gate_id-1], use_shadow=True, shadow_color=SHADOW), # TODO: shadow_color=ADAPTIVE_SHADOW 暂未实现
                        ).set_content_align('c').set_offset((4, 2))

                    for character in visit_characters:
                        chara_icon = await get_img_from_path(ASSETS_BASE_DIR, character.sd_image_path)
                        with Frame().set_content_align('lt'):
                            ImageBox(chara_icon, size=(80, None), use_alpha_blend=True)
                            if not character.is_read:
                                chara_item_icon = await get_img_from_path(ASSETS_BASE_DIR, character.memoria_image_path)
                                ImageBox(chara_item_icon, size=(40, None), use_alpha_blend=True, shadow=True).set_offset((80 - 40, 80 - 40))
                            if character.is_reservation:
                                invitation_icon = await get_img_from_path(ASSETS_BASE_DIR, f"{RESULT_ASSET_PATH}/mysekai/invitationcard.png")
                                ImageBox(invitation_icon, size=(25, None), use_alpha_blend=True, shadow=True).set_offset((10, 80 - 30))
                    Spacer(w=16, h=1)

                # 每个地区的资源
                for site_res_num in site_res_nums:
                    res_nums = site_res_num.resource_numbers
                    if not res_nums: continue
                    with HSplit().set_bg(roundrect_bg(alpha=80)).set_content_align('lt').set_item_align('lt').set_padding(16).set_sep(16):
                        site_img = await get_img_from_path(ASSETS_BASE_DIR, site_res_num.image_path)
                        ImageBox(site_img, size=(None, 85))
                        
                        with Grid(col_count=5).set_content_align('lt').set_sep(h_sep=5, v_sep=5):
                            for res_num in res_nums:
                                try:
                                    res_img = await get_img_from_path(ASSETS_BASE_DIR, res_num.image_path)
                                except Exception as e:
                                    print(e) # TODO: 没有日志
                                    res_img = None
                                if not res_img: continue
                                res_quantity = res_num.number
                                with HSplit().set_content_align('l').set_item_align('l').set_sep(5):
                                    text_color = res_num.text_color
                                    has_music_record = res_num.has_music_record
                                    if has_music_record:
                                        with Frame().set_content_align('rb'):
                                            ImageBox(res_img, size=(40, 40), use_alpha_blend=True)
                                            music_record_icon = await get_img_from_path(ASSETS_BASE_DIR, f'{RESULT_ASSET_PATH}/mysekai/music_record.png')
                                            ImageBox(music_record_icon, size=(25, 25), use_alpha_blend=True, shadow=True).set_offset((5, 5))
                                    else:
                                        ImageBox(res_img, size=(40, 40), use_alpha_blend=True)
                                    TextBox(
                                        f"{res_quantity}", 
                                        TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=text_color,
                                                    use_shadow=True, shadow_color=WHITE),
                                        overflow='clip'
                                    ).set_w(80).set_content_align('l')
    add_watermark(canvas)
    return await canvas.get_img()


# 合成mysekai家具列表图片
async def compose_mysekai_fixture_list_image(
    rqd: MysekaiFixtureListRequest
) -> Image.Image:
    # 
    mysekai_info = rqd.mysekai_info
    error_message = rqd.error_message
    # 收集进度
    progress_message = rqd.progress_message
    # 是否显示家具id
    show_id = rqd.show_id
    # 家具列表
    main_genres = rqd.main_genres
    # 获取玩家已获得的蓝图对应的家具ID
    text_color = (75, 75, 75)

    # 绘制
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16) as vs:
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
                if mysekai_info:
                    await get_mysekai_info_card(mysekai_info, error_message)

            if progress_message:
                TextBox(progress_message, 
                        TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=text_color)) \
                        .set_padding(16).set_bg(roundrect_bg(alpha=80))

            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_item_bg(roundrect_bg(alpha=80)):
                # 一级分类
                for main_genre in main_genres:
                    if len(main_genre.sub_genres) == 0: continue
                    with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_item_bg(roundrect_bg(alpha=80)).set_padding(8):
                        # 标签
                        image = await get_img_from_path(ASSETS_BASE_DIR, main_genre.image_path)    
                        with HSplit().set_content_align('c').set_item_align('c').set_sep(5).set_omit_parent_bg(True):
                            ImageBox(image, size=(None, 30), use_alpha_blend=True).set_bg(RoundRectBg(fill=(100,100,100,255), radius=2))
                            TextBox(main_genre.title, TextStyle(font=DEFAULT_HEAVY_FONT, size=20, color=text_color))
                            if main_genre.progress_message:
                                TextBox(main_genre.progress_message, TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=text_color))
                        # 二级分类
                        for sub_genre in main_genre.sub_genres:
                            if len(sub_genre.fixtures) == 0: continue
                            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_item_bg(roundrect_bg(alpha=80)).set_padding(8):
                                # 标签
                                if sub_genre.title and sub_genre.image_path and len(main_genre.sub_genres) > 1: # 无二级分类或只有一个二级分类的不加标签
                                    image = await get_img_from_path(ASSETS_BASE_DIR, sub_genre.image_path)    
                                    with HSplit().set_content_align('c').set_item_align('c').set_sep(5).set_omit_parent_bg(True):
                                        ImageBox(image, size=(None, 23), use_alpha_blend=True).set_bg(RoundRectBg(fill=(100,100,100,255), radius=2))
                                        TextBox(sub_genre.title, TextStyle(font=DEFAULT_HEAVY_FONT, size=15, color=text_color))
                                        if sub_genre.progress_message:
                                            TextBox(sub_genre.progress_message, TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=text_color))
                                
                                # 通过角色id获取角色图标 TODO: 或许这里可以有更好的方法
                                async def get_chara_icon_by_chara_id(cid:int)->Image.Image:
                                    nickname = {
                                        1:"ick", 2:"saki", 3:"hnm", 4:"shiho",
                                        5:"mnr", 6:"hrk", 7:"airi", 8:"szk",
                                        9:"khn", 10:"an", 11:"akt", 12:"toya",
                                        13:"tks", 14:"emu", 15:"nene", 16:"rui",
                                        17:"knd", 18:"mfy", 19:"ena", 20:"mzk",
                                        21:"miku", 22:"rin", 23:"len", 24:"luka", 25:"meiko", 26:"kaito"
                                    }.get(cid)
                                    return await get_img_from_path(ASSETS_BASE_DIR, f"{RESULT_ASSET_PATH}/chara_icon/{nickname}.png")
                                # 绘制单个家具
                                async def draw_single_fixture(fixture: MysekaiSingleFixture):
                                    f_sz = 30
                                    image = await get_img_from_path(ASSETS_BASE_DIR, fixture.image_path)
                                    with VSplit().set_content_align('c').set_item_align('c').set_sep(0):
                                        with Frame().set_content_align('rt'):
                                            ImageBox(image, size=(f_sz, f_sz), use_alpha_blend=True)
                                            if fixture.character_id is not None:
                                                chara_icon = await get_chara_icon_by_chara_id(fixture.character_id)
                                                ImageBox(chara_icon, size=(12, 12), use_alpha_blend=False)
                                            if not fixture.obtained:
                                                Spacer(w=f_sz, h=f_sz).set_bg(RoundRectBg(fill=(0,0,0,80), radius=2))
                                        if show_id:
                                            TextBox(f"{fixture.id}", TextStyle(font=DEFAULT_FONT, size=10, color=(50, 50, 50)))
                                COL_COUNT = 20 # 一行20个
                                sep = 3
                                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(sep):
                                    for cur_y in range(0, len(sub_genre.fixtures), COL_COUNT):
                                        with HSplit().set_content_align('lt').set_item_align('lt').set_sep(sep):
                                            for single_fixture in sub_genre.fixtures[cur_y:cur_y+COL_COUNT]:
                                                await draw_single_fixture(single_fixture)
                                    
        pass# 到这里绘制完毕
    add_watermark(canvas)

    # # 缓存非玩家查询的msf
    # cache_key = None
    # if not qid and show_id and not only_craftable:
    #     cache_key = f"{ctx.region}_msf"

    return await canvas.get_img()