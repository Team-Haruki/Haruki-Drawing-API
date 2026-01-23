from PIL import Image, ImageDraw
from src.sekai.base.configs import (
    ASSETS_BASE_DIR,
    DEFAULT_FONT,
    DEFAULT_BOLD_FONT,
    RESULT_ASSET_PATH,
    DEFAULT_HEAVY_FONT
)
from src.sekai.base.draw import(
    BG_PADDING,
    roundrect_bg,
    SEKAI_BLUE_BG,
    add_watermark
)
from src.sekai.base.utils import (
    get_img_from_path,
)
from src.sekai.base.plot import(
    Canvas,
    VSplit,
    HSplit,
    Frame,
    ImageBg,
    ImageBox,
    TextStyle,
    TextBox,
    Spacer,
    Grid,
    RoundRectBg,
    Widget,
    FillBg
)
from src.sekai.base.painter import(
    BLACK,
    WHITE,
    color_code_to_rgb,
    lerp_color,
    ADAPTIVE_SHADOW
)
from src.sekai.profile.drawer import (
    get_profile_card
)


from .model import *

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
    profile = rqd.profile
    # 天气
    phenoms = rqd.phenoms
    # 到访角色列表
    visit_characters = rqd.visit_characters
    # 地区资源列表
    site_res_nums = rqd.site_resource_numbers

    with Canvas(bg=bg).set_padding(BG_PADDING).set_content_align('c') as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16) as vs:

            with HSplit().set_sep(28).set_content_align('lb'):
                await get_profile_card(profile)

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
                        
                        # 确保背景颜色是元组
                        bg_fill = tuple(phenom.background_fill) if isinstance(phenom.background_fill, (list, tuple)) else phenom.background_fill
                        txt_fill = tuple(phenom.text_fill) if isinstance(phenom.text_fill, (list, tuple)) else phenom.text_fill
                        
                        with Frame():
                            with VSplit().set_content_align('c').set_item_align('c').set_sep(5).set_bg(roundrect_bg(fill=bg_fill)).set_padding(8):
                                TextBox(phenom.text, TextStyle(font=DEFAULT_BOLD_FONT, size=15, color=txt_fill)).set_w(60).set_content_align('c')
                                ImageBox(phenom_img, size=(None, 50), use_alpha_blend=True) 
            
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_padding(16).set_bg(roundrect_bg(alpha=80)):
                # 到访角色列表
                with HSplit().set_bg(roundrect_bg(alpha=80)).set_content_align('c').set_item_align('c').set_padding(16).set_sep(16):
                    gate_id = rqd.gate_id
                    gate_icon_path = rqd.gate_icon_path or f"{RESULT_ASSET_PATH}/mysekai/gate_icon/gate_{gate_id}.png"
                    gate_icon = await get_img_from_path(ASSETS_BASE_DIR, gate_icon_path)
                    gate_level = rqd.gate_level
                    with Frame().set_size((64, 64)).set_margin((16, 0)).set_content_align('rb'):
                        ImageBox(gate_icon, size=(64, 64), use_alpha_blend=True, shadow=True).set_offset((0, -4))
                        TextBox(
                            f"Lv.{gate_level}", 
                            TextStyle(DEFAULT_FONT, 16, UNIT_COLORS[gate_id-1], use_shadow=True, shadow_color=ADAPTIVE_SHADOW),
                        ).set_content_align('c').set_offset((4, 2))

                    for character in visit_characters:
                        chara_icon = await get_img_from_path(ASSETS_BASE_DIR, character.sd_image_path)
                        with Frame().set_content_align('lt'):
                            ImageBox(chara_icon, size=(80, None), use_alpha_blend=True)
                            if not character.is_read:
                                chara_item_icon = await get_img_from_path(ASSETS_BASE_DIR, character.memoria_image_path)
                                ImageBox(chara_item_icon, size=(40, None), use_alpha_blend=True, shadow=True).set_offset((80 - 40, 80 - 40))
                            if character.is_reservation:
                                invitation_icon_path = character.reservation_icon_path or f"{RESULT_ASSET_PATH}/mysekai/invitationcard.png"
                                invitation_icon = await get_img_from_path(ASSETS_BASE_DIR, invitation_icon_path)
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
                                            music_record_icon_path = res_num.music_record_icon_path or f'{RESULT_ASSET_PATH}/mysekai/music_record.png'
                                            music_record_icon = await get_img_from_path(ASSETS_BASE_DIR, music_record_icon_path)
                                            ImageBox(music_record_icon, size=(25, 25), use_alpha_blend=True, shadow=True).set_offset((5, 5))
                                    else:
                                        ImageBox(res_img, size=(40, 40), use_alpha_blend=True)
                                    
                                    # 确保文字颜色是元组
                                    t_color = tuple(text_color) if isinstance(text_color, (list, tuple)) else text_color
                                    TextBox(
                                        f"{res_quantity}", 
                                        TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=t_color,
                                                    use_shadow=True, shadow_color=WHITE),
                                        overflow='clip'
                                    ).set_w(80).set_content_align('l')
    add_watermark(canvas)
    return await canvas.get_img()


# 合成mysekai家具列表图片
async def compose_mysekai_fixture_list_image(
    rqd: MysekaiFixtureListRequest
) -> Image.Image:
    r"""compose_mysekai_fixture_list_image

    合成我的世界家具列表图片

    Args
    ----
    rqd : MysekaiFixtureListRequest
        合成我的世界家具列表图片所必须的数据
    
    Returns
    -------
    PIL.Image.Image
    """
    # 个人信息
    profile = rqd.profile
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
                if profile:
                    await get_profile_card(profile)

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
                            TextBox(main_genre.name, TextStyle(font=DEFAULT_HEAVY_FONT, size=20, color=text_color))
                            if main_genre.progress_message:
                                TextBox(main_genre.progress_message, TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=text_color))
                        # 二级分类
                        for sub_genre in main_genre.sub_genres:
                            if len(sub_genre.fixtures) == 0: continue
                            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_item_bg(roundrect_bg(alpha=80)).set_padding(8):
                                # 标签
                                if sub_genre.name and sub_genre.image_path and len(main_genre.sub_genres) > 1: # 无二级分类或只有一个二级分类的不加标签
                                    image = await get_img_from_path(ASSETS_BASE_DIR, sub_genre.image_path)    
                                    with HSplit().set_content_align('c').set_item_align('c').set_sep(5).set_omit_parent_bg(True):
                                        ImageBox(image, size=(None, 23), use_alpha_blend=True).set_bg(RoundRectBg(fill=(100,100,100,255), radius=2))
                                        TextBox(sub_genre.name, TextStyle(font=DEFAULT_HEAVY_FONT, size=15, color=text_color))
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
                                    icon_path = fixture.chara_icon_path or f"{RESULT_ASSET_PATH}/chara_icon/{nickname}.png"
                                    return await get_img_from_path(ASSETS_BASE_DIR, icon_path)
                                # 绘制单个家具
                                async def draw_single_fixture(fixture: MysekaiFixture):
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

    return await canvas.get_img()

async def get_chara_icon_by_chara_unit_id(cuid: int)->Image.Image:
    r"""get_chara_icon_by_chara_unit_id 
    
    用cuid获取角色图标

    Args
    ----
    cuid : int
        角色队伍id

    Returns
    -------
    PIL.Image.Image
    """
    nickname = {
        1:"ick", 2:"saki", 3:"hnm", 4:"shiho", 5:"mnr", 6:"hrk", 7:"airi", 8:"szk",
        9:"khn", 10:"an", 11:"akt", 12:"toya", 13:"tks", 14:"emu", 15:"nene", 16:"rui", 
        17:"knd", 18:"mfy", 19:"ena", 20:"mzk", 21:"miku", 22:"rin", 23:"len", 24:"luka", 25:"meiko", 26:"kaito",
        27:"miku_light_sound", 28: "miku_idol", 29:"miku_street", 30: "miku_theme_park", 31:"miku_school_refusal",
        32:"rin", 33:"rin", 34:"rin", 35:"rin", 36:"rin", 37:"len", 38:"len", 39:"len", 40:"len", 41:"len",
        42:"luka", 43:"luka", 44:"luka", 45:"luka", 46:"luka", 47:"meiko", 48:"meiko", 49:"meiko", 50:"meiko", 51:"meiko",
        52:"kaito", 53:"kaito", 54:"kaito", 55:"kaito", 56:"kaito"}.get(cuid)
    return await get_img_from_path(ASSETS_BASE_DIR, f"{RESULT_ASSET_PATH}/chara_icon/{nickname}.png")

# 获取mysekai家具详情卡片控件 返回Widget
async def get_mysekai_fixture_detail_image_card(
        rqd: MysekaiFixtureDetailRequest
) -> Widget:
    r"""get_mysekai_fixture_detail_image_card

    获取我的世界家具详情卡片控件

    Args
    ----
    rqd : MysekaiFixtureDetailRequest
        获取我的世界家具详情卡片控件所必须的数据
    
    Returns
    -------
    Widget
    """
    # 标题
    title_text = rqd.title
    # 家具
    color_images = rqd.images
    fsize = rqd.size
    basic_info = rqd.basic_info
    cost_materials = rqd.cost_materials
    recycle_materials = rqd.recycle_materials
    reaction_character_groups = rqd.reaction_character_groups
    tags = rqd.tags
    friendcodes = rqd.friendcodes
    friendcode_source = rqd.friendcode_source
    w = 600
    with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(16) as vs:
        # 标题
        TextBox(title_text, TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(20, 20, 20)), use_real_line_count=True).set_padding(8).set_bg(roundrect_bg(alpha=80)).set_w(w+16)
        # 缩略图列表
        with Grid(col_count=5).set_content_align('c').set_item_align('c').set_sep(8, 4).set_padding(8).set_bg(roundrect_bg(alpha=80)).set_w(w+16):
            for color_img in color_images:
                img = await get_img_from_path(ASSETS_BASE_DIR, color_img.image_path)
                with VSplit().set_content_align('c').set_item_align('c').set_sep(8):
                    ImageBox(img, size=(None, 100), use_alpha_blend=True, shadow=True)
                    if color_img.color_code:
                        Frame().set_size((100, 20)).set_bg(RoundRectBg(
                            fill=color_code_to_rgb(color_img.color_code), 
                            radius=4,
                            stroke=(150, 150, 150, 255), stroke_width=3,
                        ))
        # 基本信息
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(8).set_bg(roundrect_bg(alpha=80)).set_w(w+16):
            font_size, text_color = 18, (100, 100, 100)
            style = TextStyle(font=DEFAULT_FONT, size=font_size, color=text_color)
            with HSplit().set_content_align('c').set_item_align('c').set_sep(2):
                TextBox(f"【类型】", style)
                main_genre_image = await get_img_from_path(ASSETS_BASE_DIR, rqd.main_genre_image_path)
                ImageBox(main_genre_image, size=(None, font_size+2), use_alpha_blend=True).set_bg(RoundRectBg(fill=(150,150,150,255), radius=2))
                TextBox(rqd.main_genre_name, style)
                if rqd.sub_genre_name:
                    TextBox(f" > ", TextStyle(font=DEFAULT_HEAVY_FONT, size=font_size, color=text_color))
                    if rqd.sub_genre_image_path:
                        sub_genre_image = await get_img_from_path(ASSETS_BASE_DIR, rqd.sub_genre_image_path)
                        ImageBox(sub_genre_image, size=(None, font_size+2), use_alpha_blend=True).set_bg(RoundRectBg(fill=(150,150,150,255), radius=2))
                    TextBox(rqd.sub_genre_name, style)
                TextBox(f"【大小】长x宽x高={fsize['width']}x{fsize['depth']}x{fsize['height']}", style)
            if basic_info:
                for row in basic_info:
                    with HSplit().set_content_align('c').set_item_align('c').set_sep(2):
                        for tag in row:
                            TextBox(tag, style)
                

        # 制作材料
        if cost_materials:
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(12).set_bg(roundrect_bg(alpha=80)):
                TextBox("制作材料", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(50, 50, 50))).set_w(w)
                with Grid(col_count=8).set_content_align('lt').set_sep(6, 6):
                    for material in cost_materials:
                        img = await get_img_from_path(ASSETS_BASE_DIR, material.image_path)
                        with VSplit().set_content_align('c').set_item_align('c').set_sep(2):
                            ImageBox(img, size=(50, 50), use_alpha_blend=True)
                            TextBox(f"x{material.quantity}", TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(100, 100, 100)))

        # 回收材料
        if recycle_materials:
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(12).set_bg(roundrect_bg(alpha=80)):
                TextBox("回收材料", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(50, 50, 50))).set_w(w)
                with Grid(col_count=8).set_content_align('lt').set_sep(6, 6):
                    for material in recycle_materials:
                        img = await get_img_from_path(ASSETS_BASE_DIR, material.image_path)
                        with VSplit().set_content_align('c').set_item_align('c').set_sep(2):
                            ImageBox(img, size=(50, 50), use_alpha_blend=True)
                            TextBox(f"x{material.quantity}", TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(100, 100, 100)))

        # 交互角色
        if reaction_character_groups:
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(12).set_bg(roundrect_bg(alpha=80)):
                TextBox("角色互动", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(50, 50, 50))).set_w(w)
                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8):
                    for chara_groups in reaction_character_groups:
                        if not chara_groups.number: continue
                        col_num_dict = { 1: 10, 2: 5, 3: 4, 4: 2 }
                        col_num = col_num_dict[chara_groups.number]
                        with Grid(col_count=col_num).set_content_align('c').set_sep(6, 4):
                                    for group_idx, cuid in enumerate(character_uint_ids):
                                        c_icon_path = None
                                        if rqd.chara_icon_path_groups and len(rqd.chara_icon_path_groups) > chara_groups_idx:
                                            if len(rqd.chara_icon_path_groups[chara_groups_idx]) > group_idx:
                                                c_icon_path = rqd.chara_icon_path_groups[chara_groups_idx][group_idx]
                                        
                                        img = await get_chara_icon_by_chara_unit_id(cuid) if not c_icon_path else await get_img_from_path(ASSETS_BASE_DIR, c_icon_path)
                                        ImageBox(img, size=(40, 40), use_alpha_blend=True)

        # 标签
        if tags:
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(8).set_bg(roundrect_bg(alpha=80)).set_w(w+16):
                TextBox("标签", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(50, 50, 50))).set_w(w)
                for row in tags:
                    tag_text = ""
                    for tag in row: tag_text += f"【{tag}】"
                    TextBox(tag_text, TextStyle(font=DEFAULT_FONT, size=18, color=(100, 100, 100)), line_count=10, use_real_line_count=True).set_w(w)

        # 抄写好友码
        if friendcodes:
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(8).set_bg(roundrect_bg(alpha=80)).set_w(w+16):
                with HSplit().set_content_align('lb').set_item_align('lb').set_sep(8).set_w(w):
                    TextBox("抄写蓝图可前往", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(50, 50, 50)))
                    TextBox(friendcode_source, TextStyle(font=DEFAULT_FONT, size=14, color=(75, 75, 75)))
                for row in friendcodes:
                    code_text = "      ".join(row)
                    TextBox(code_text, TextStyle(font=DEFAULT_FONT, size=18, color=(100, 100, 100)), line_count=10, use_real_line_count=True).set_w(w)
    return vs

# 获取mysekai家具详情
async def compose_mysekai_fixture_detail_image(rqds: List[MysekaiFixtureDetailRequest]) -> Image.Image:
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_item_bg(roundrect_bg(alpha=80)):
            for rqd in rqds:
                await get_mysekai_fixture_detail_image_card(rqd)
    add_watermark(canvas)
    return await canvas.get_img()


# 合成mysekai门升级材料图片
async def compose_mysekai_door_upgrade_image(
        rqd: MysekaiDoorUpgradeRequest
) -> Image.Image:
    r"""compose_mysekai_door_upgrade_image

    合成我的世界大门升级材料图

    Args
    ----
    rqd : MysekaiDoorUpgradeRequest
        必需的数据
    
    Returns
    -------
    PIL.Image.Image
    """
    # 个人信息
    profile = rqd.profile
    # 大门升级材料
    gate_materials = rqd.gate_materials
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
            # 个人信息
            if profile:
                await get_profile_card(profile)
            with HSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_bg(roundrect_bg(alpha=80)).set_padding(8):
                # 每个门
                for gate_level_materials in gate_materials:
                    gid = gate_level_materials.id
                    gate_icon_path = f"{RESULT_ASSET_PATH}/mysekai/gate_icon/gate_{gid}.png" # Default
                    # Note: MysekaiGateMaterials doesn't have gate_icon_path in model yet, but rqd does in some places.
                    # Looking at the model, MysekaiGateMaterials doesn't have it.
                    gate_icon = await get_img_from_path(ASSETS_BASE_DIR, gate_icon_path)
                    with VSplit().set_content_align('c').set_item_align('c').set_sep(8).set_item_bg(roundrect_bg(alpha=80)).set_padding(8):
                        spec_lv = gate_level_materials.level
                        with HSplit().set_content_align('c').set_item_align('c').set_omit_parent_bg(True):
                            ImageBox(gate_icon, size=(None, 40))
                            if spec_lv:
                                color = lerp_color(UNIT_COLORS[gid - 1], BLACK, 0.2)
                                TextBox(f"Lv.{spec_lv}", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=color, use_shadow=True, shadow_color=ADAPTIVE_SHADOW))
                        # 每个等级
                        for level_items in gate_level_materials.level_materials:

                            with HSplit().set_content_align('l').set_item_align('l').set_sep(8).set_padding(8):
                                TextBox(f"{level_items.level}", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=level_items.color), overflow='clip').set_w(32)
                                # 每个材料
                                for item in level_items.items:
                                    with VSplit().set_content_align('c').set_item_align('c').set_sep(4):
                                        img = await get_img_from_path(ASSETS_BASE_DIR, item.image_path)
                                        with Frame():
                                            sz = 64
                                            ImageBox(img, size=(sz, sz))
                                            # 确保颜色是元组
                                            txt_color = tuple(item.color) if isinstance(item.color, (list, tuple)) else item.color
                                            TextBox(f"x{item.quantity}", TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(50, 50, 50))) \
                                                .set_offset((sz, sz)).set_offset_anchor('rb')
                                        TextBox(item.sum_quantity, TextStyle(font=DEFAULT_BOLD_FONT, size=15, color=txt_color))
    add_watermark(canvas)
    return await canvas.get_img()


# 合成mysekai唱片列表
async def compose_mysekai_musicrecord_image(
        rqd: MysekaiMusicrecordRequest
) -> Image.Image:
    r"""compose_mysekai_musicrecord_image

    合成我的世界唱片列表

    Args
    ----
    rqd : MysekaiMusicrecordRequest
        绘制我的世界唱片收集图所必需的数据

    Returns
    -------
    PIL.Image.Image
    """        
    profile = rqd.profile
    category_musicrecords = rqd.category_musicrecords

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16) as vs:
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
                await get_profile_card(profile)
                if rqd.progress_message:
                    TextBox(rqd.progress_message, TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(100, 100, 100))) \
                        .set_padding(16).set_bg(roundrect_bg(alpha=80))

                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_item_bg(roundrect_bg()):
                   for category_music in category_musicrecords:
                        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(5).set_item_bg(roundrect_bg()).set_padding(8):
                            # 标签
                            tag = category_music.tag
                            with HSplit().set_content_align('c').set_item_align('c').set_sep(5).set_omit_parent_bg(True):
                                if (unit:= MUSIC_TAG_UNIT_MAP[tag]):
                                    tag_icon_path = category_music.tag_icon_path or f"{RESULT_ASSET_PATH}/icon_{unit}.png"
                                    tag_icon = await get_img_from_path(ASSETS_BASE_DIR, tag_icon_path)
                                    ImageBox(tag_icon, size=(None, 30))
                                else:
                                    TextBox("其他", TextStyle(font=DEFAULT_HEAVY_FONT, size=20, color=(100, 100, 100)))
                                if category_music.progress_message:
                                    TextBox(category_music.progress_message, TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(100, 100, 100)))

                            # 歌曲列表
                            sz = 30
                            with Grid(col_count=20).set_content_align('lt').set_item_align('lt').set_sep(3, 3).set_padding(8):
                                for musicrecord in category_music.musicrecords:
                                    with VSplit().set_content_align('c').set_item_align('c').set_sep(3):
                                        with Frame():
                                            img = await get_img_from_path(ASSETS_BASE_DIR, musicrecord.image_path)
                                            ImageBox(img, size=(sz, sz))
                                            if not musicrecord.obtained:
                                                Spacer(w=sz, h=sz).set_bg(FillBg((0,0,0,120)))
                                        if musicrecord.id:
                                            TextBox(f"{musicrecord.id}", TextStyle(font=DEFAULT_FONT, size=10, color=(50, 50, 50)))

    add_watermark(canvas)
    return await canvas.get_img()


# 合成mysekai对话列表图片
async def compose_mysekai_talk_list_image(
        rqd: MysekaiTalkListRequest
) -> Image.Image:
    r"""compose_mysekai_talk_list_image

    合成我的世界对话列表图片

    Args
    ----
    rqd : MysekaiTalkListRequest
        绘制我的世界对话列表所必需的数据

    Returns
    -------
    PIL.Image.Image
    """
    profile = rqd.profile # 个人信息
    sd_image_path = rqd.sd_image_path
    progress_message = rqd.progress_message
    prompt_message = rqd.prompt_message
    show_id = rqd.show_id
    single_main_genres = rqd.single_main_genres
    multi_reads = rqd.multi_reads
    
    # 绘制单个家具
    async def draw_single_fid(fixture: MysekaiFixture):
        f_sz = 30
        image = await get_img_from_path(ASSETS_BASE_DIR, fixture.image_path)
        with VSplit().set_content_align('c').set_item_align('c').set_sep(2):
            with Frame():
                ImageBox(image, size=(None, f_sz), use_alpha_blend=True)
                if not fixture.obtained:
                    Spacer(w=f_sz, h=f_sz).set_bg(RoundRectBg(fill=(0,0,0,80), radius=2))
            if show_id:
                TextBox(f"{fixture.id}", TextStyle(font=DEFAULT_FONT, size=10, color=(50, 50, 50)))

    # 绘制包含多个家具组合以及未读情况
    async def draw_fids(fixture_talk_read: MysekaiTalkFixtures):
        with Frame().set_content_align('rb'):
            with HSplit().set_content_align('c').set_item_align('c').set_sep(2).set_bg(roundrect_bg(radius=4, alpha=80)).set_padding(4):
                for fixture in fixture_talk_read.fixtures:
                    await draw_single_fid(fixture)
            if fixture_talk_read.noread_num > 1:
                TextBox(f"x{fixture_talk_read.noread_num}", TextStyle(font=DEFAULT_FONT, size=12, color=(255, 0, 0))).set_offset((5, 5))

    text_color = (75, 75, 75)
                                        
    # 绘制
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16) as vs:
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
                if profile:
                    await get_profile_card(profile)

            # 进度
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(16).set_bg(roundrect_bg(alpha=80)):
                with HSplit().set_content_align('l').set_item_align('l').set_sep(5):
                    chara_icon = await get_img_from_path(ASSETS_BASE_DIR, sd_image_path)
                    ImageBox(chara_icon, size=(None, 60))
                    if progress_message:
                        TextBox(progress_message, 
                                TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=text_color))
                if prompt_message:
                    TextBox(prompt_message, TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=text_color))
            
            # 单人家具
            TextBox(f"单人对话家具", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=text_color)) \
                .set_padding(12).set_bg(roundrect_bg(alpha=80))

            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16).set_item_bg(roundrect_bg(alpha=80)):
                has_single = False
                # 一级分类
                for main_genre in single_main_genres:
                    if len(main_genre.sub_genres) == 0: continue
                    has_single = True

                    with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(8):
                        # 标签
                        main_genre_name = main_genre.name
                        main_genre_image = await get_img_from_path(ASSETS_BASE_DIR, main_genre.image_path)
                        with HSplit().set_content_align('c').set_item_align('c').set_sep(5).set_omit_parent_bg(True):
                            ImageBox(main_genre_image, size=(None, 30), use_alpha_blend=True).set_bg(RoundRectBg(fill=(100,100,100,255), radius=2))
                            TextBox(main_genre_name, TextStyle(font=DEFAULT_HEAVY_FONT, size=20, color=text_color))

                        # 家具列表
                        for sub_genre in main_genre.sub_genres:
                            if len(sub_genre) == 0: continue
                            COL_COUNT, cur_idx = 15, 0
                            sep = 5 # if cid else 3， 这个永远为True
                            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(sep):
                                while cur_idx < len(sub_genre):
                                    cur_x = 0
                                    with HSplit().set_content_align('lt').set_item_align('lt').set_sep(sep):
                                        while cur_x < COL_COUNT:
                                            fixtures = sub_genre[cur_idx]
                                            await draw_fids(fixtures)
                                            cur_x += len(fixtures.fixtures)
                                            cur_idx += 1
                                            if cur_idx >= len(sub_genre):
                                                break
                if not has_single:
                    TextBox("全部已读", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(50, 150, 50))).set_padding(16)

            # 多人家具
            TextBox(f"多人对话家具", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=text_color)) \
                .set_padding(12).set_bg(roundrect_bg(alpha=80))    

            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(8).set_bg(roundrect_bg(alpha=80)):
                has_multi = False
                for multi_read in multi_reads:
                    if not multi_read.fixtures or multi_read.noread_num <= 0:
                        continue
                    has_multi = True
                    with HSplit().set_content_align('lt').set_item_align('l').set_sep(6):
                        await draw_fids(multi_read)
                        for group_idx, cuids in enumerate(multi_read.character_ids):
                            with HSplit().set_content_align('lt').set_item_align('lt').set_sep(5).set_padding(4).set_bg(roundrect_bg(alpha=80)):
                                for cuid_idx, cuid in enumerate(cuids):
                                    c_icon_path = None
                                    if multi_read.chara_icon_path_groups and len(multi_read.chara_icon_path_groups) > group_idx:
                                        if len(multi_read.chara_icon_path_groups[group_idx]) > cuid_idx:
                                            c_icon_path = multi_read.chara_icon_path_groups[group_idx][cuid_idx]
                                    
                                    img = await get_chara_icon_by_chara_unit_id(cuid) if not c_icon_path else await get_img_from_path(ASSETS_BASE_DIR, c_icon_path)
                                    ImageBox(img, size=(None, 36))
                if not has_multi:
                    TextBox("全部已读", TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(50, 150, 50))).set_padding(8)

    add_watermark(canvas)
    return await canvas.get_img()