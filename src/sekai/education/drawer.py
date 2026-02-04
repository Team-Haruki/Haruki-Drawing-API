"""
Education 模块绘图函数

提供挑战Live详情、加成详情、区域道具升级材料、羁绊等级、队长次数等图片的绘制功能。
"""

from PIL import Image

from src.sekai.base.draw import (
    BG_PADDING,
    SEKAI_BLUE_BG,
    Canvas,
    TextStyle,
    add_watermark,
    roundrect_bg,
)
from src.sekai.base.plot import (
    FillBg,
    Frame,
    Grid,
    HSplit,
    ImageBox,
    LinearGradient,
    RoundRectBg,
    Spacer,
    TextBox,
    VSplit,
)
from src.sekai.base.utils import get_img_from_path
from src.sekai.profile.drawer import get_detailed_profile_card
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT

# 从 model.py 导入数据模型
from .model import (
    AreaItemUpgradeMaterialsRequest,
    BondsRequest,
    ChallengeLiveDetailsRequest,
    LeaderCountRequest,
    PowerBonusDetailRequest,
)

# ========== 挑战Live详情 ==========


async def compose_challenge_live_detail_image(rqd: ChallengeLiveDetailsRequest) -> Image.Image:
    """合成挑战Live详情图片

    Args:
        rqd: 挑战Live详情请求数据

    Returns:
        生成的挑战Live详情图片
    """
    profile = rqd.profile
    character_challenges = rqd.character_challenges
    max_score = rqd.max_score

    header_h, row_h = 56, 48
    header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(25, 25, 25, 255))
    text_style = TextStyle(font=DEFAULT_FONT, size=20, color=(50, 50, 50, 255))
    w1, w2, w3, w4, w5, w6 = 80, 80, 150, 300, 80, 80

    # 获取图标
    jewel_icon = await get_img_from_path(ASSETS_BASE_DIR, rqd.jewel_icon_path) if rqd.jewel_icon_path else None
    shard_icon = await get_img_from_path(ASSETS_BASE_DIR, rqd.shard_icon_path) if rqd.shard_icon_path else None

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            await get_detailed_profile_card(profile)

            with VSplit().set_content_align("c").set_item_align("c").set_sep(8).set_padding(16).set_bg(roundrect_bg()):
                # 标题行
                with (
                    HSplit()
                    .set_content_align("c")
                    .set_item_align("c")
                    .set_sep(8)
                    .set_h(header_h)
                    .set_padding(4)
                    .set_bg(roundrect_bg())
                ):
                    TextBox("角色", header_style).set_w(w1).set_content_align("c")
                    TextBox("等级", header_style).set_w(w2).set_content_align("c")
                    TextBox("分数", header_style).set_w(w3).set_content_align("c")
                    TextBox(f"进度(上限{max_score // 10000}w)", header_style).set_w(w4).set_content_align("c")
                    with Frame().set_w(w5).set_content_align("c"):
                        if jewel_icon:
                            ImageBox(jewel_icon, size=(None, 40))
                    with Frame().set_w(w6).set_content_align("c"):
                        if shard_icon:
                            ImageBox(shard_icon, size=(None, 40))

                # 角色数据行
                for idx, challenge in enumerate(character_challenges):
                    bg_color = (255, 255, 255, 150) if idx % 2 == 0 else (255, 255, 255, 100)

                    rank_text = str(challenge.rank) if challenge.rank else "-"
                    score_text = str(challenge.score) if challenge.score else "-"
                    jewel_text = str(challenge.jewel)
                    shard_text = str(challenge.shard)

                    chara_icon = await get_img_from_path(ASSETS_BASE_DIR, challenge.chara_icon_path)

                    with (
                        HSplit()
                        .set_content_align("c")
                        .set_item_align("c")
                        .set_sep(8)
                        .set_h(row_h)
                        .set_padding(4)
                        .set_bg(roundrect_bg(fill=bg_color))
                    ):
                        with Frame().set_w(w1).set_content_align("c"):
                            if chara_icon:
                                ImageBox(chara_icon, size=(None, 40))

                        TextBox(rank_text, text_style).set_w(w2).set_content_align("c")
                        TextBox(score_text, text_style.replace(font=DEFAULT_BOLD_FONT)).set_w(w3).set_content_align("c")

                        with Frame().set_w(w4).set_content_align("lt"):
                            x = challenge.score or 0
                            progress = max(min(x / max_score, 1), 0) if max_score > 0 else 0
                            total_w, total_h, border = w4, 14, 2
                            progress_w = int((total_w - border * 2) * progress)
                            progress_h = total_h - border * 2

                            color = (255, 50, 50, 255)
                            if x > 2500000:
                                color = (100, 255, 100, 255)
                            elif x > 2000000:
                                color = (255, 255, 100, 255)
                            elif x > 1500000:
                                color = (255, 200, 100, 255)
                            elif x > 1000000:
                                color = (255, 150, 100, 255)
                            elif x > 500000:
                                color = (255, 100, 100, 255)

                            if progress > 0:
                                Spacer(w=total_w, h=total_h).set_bg(
                                    RoundRectBg(fill=(100, 100, 100, 255), radius=total_h // 2)
                                )
                                Spacer(w=progress_w, h=progress_h).set_bg(
                                    RoundRectBg(fill=color, radius=(total_h - border) // 2)
                                ).set_offset((border, border))

                                def draw_line(line_x: int):
                                    p = line_x / max_score if max_score > 0 else 0
                                    if p <= 0 or p >= 1:
                                        return
                                    lx = int((total_w - border * 2) * p)
                                    line_color = (100, 100, 100, 255) if line_x < x else (150, 150, 150, 255)
                                    Spacer(w=1, h=total_h // 2 - 1).set_bg(FillBg(line_color)).set_offset(
                                        (border + lx - 1, total_h // 2)
                                    )

                                for line_x in range(0, max_score, 500000):
                                    draw_line(line_x)
                            else:
                                Spacer(w=total_w, h=total_h).set_bg(
                                    RoundRectBg(fill=(100, 100, 100, 100), radius=total_h // 2)
                                )

                        TextBox(jewel_text, text_style).set_w(w5).set_content_align("c")
                        TextBox(shard_text, text_style).set_w(w6).set_content_align("c")

    add_watermark(canvas)
    return await canvas.get_img()


# ========== 加成详情 ==========


async def compose_power_bonus_detail_image(rqd: PowerBonusDetailRequest) -> Image.Image:
    """合成加成详情图片

    Args:
        rqd: 加成详情请求数据

    Returns:
        生成的加成详情图片
    """
    profile = rqd.profile
    chara_bonuses = rqd.chara_bonuses
    unit_bonuses = rqd.unit_bonuses
    attr_bonuses = rqd.attr_bonuses

    header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(25, 25, 25, 255))
    text_style = TextStyle(font=DEFAULT_FONT, size=16, color=(100, 100, 100, 255))

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            await get_detailed_profile_card(profile)

            with (
                VSplit()
                .set_content_align("lt")
                .set_item_align("lt")
                .set_sep(8)
                .set_item_bg(roundrect_bg())
                .set_bg(roundrect_bg())
                .set_padding(16)
            ):
                # 角色加成 - 分组显示
                cid_parts = [range(0, 4), range(4, 8), range(8, 12), range(12, 16), range(16, 20), range(20, 26)]
                for cid_range in cid_parts:
                    bonuses_group = [chara_bonuses[i] for i in cid_range if i < len(chara_bonuses)]
                    if not bonuses_group:
                        continue
                    with Grid(col_count=2).set_content_align("l").set_item_align("l").set_sep(20, 4).set_padding(16):
                        for bonus in bonuses_group:
                            chara_icon = await get_img_from_path(ASSETS_BASE_DIR, bonus.chara_icon_path)
                            with HSplit().set_content_align("l").set_item_align("l").set_sep(4):
                                if chara_icon:
                                    ImageBox(chara_icon, size=(None, 40))
                                TextBox(f"{bonus.total:.1f}%", header_style).set_w(100).set_content_align(
                                    "r"
                                ).set_overflow("clip")
                                detail = (
                                    f"区域道具{bonus.area_item:.1f}%"
                                    f" + 角色等级{bonus.rank:.1f}%"
                                    f" + 烤森玩偶{bonus.fixture:.1f}%"
                                )
                                TextBox(detail, text_style)

                # 组合加成
                with Grid(col_count=3).set_content_align("l").set_item_align("l").set_sep(20, 4).set_padding(16):
                    for bonus in unit_bonuses:
                        unit_icon = await get_img_from_path(ASSETS_BASE_DIR, bonus.unit_icon_path)
                        with HSplit().set_content_align("l").set_item_align("l").set_sep(4):
                            if unit_icon:
                                ImageBox(unit_icon, size=(None, 40))
                            TextBox(f"{bonus.total:.1f}%", header_style).set_w(100).set_content_align("r").set_overflow(
                                "clip"
                            )
                            detail = f"区域道具{bonus.area_item:.1f}% + 烤森门{bonus.gate:.1f}%"
                            TextBox(detail, text_style)

                # 属性加成
                with Grid(col_count=5).set_content_align("l").set_item_align("l").set_sep(20, 4).set_padding(16):
                    for bonus in attr_bonuses:
                        attr_icon = await get_img_from_path(ASSETS_BASE_DIR, bonus.attr_icon_path)
                        with HSplit().set_content_align("l").set_item_align("l").set_sep(4):
                            if attr_icon:
                                ImageBox(attr_icon, size=(None, 40))
                            TextBox(f"{bonus.total:.1f}%", header_style).set_w(100).set_content_align("r").set_overflow(
                                "clip"
                            )

    add_watermark(canvas)
    return await canvas.get_img()


# ========== 区域道具升级材料 ==========


def _get_quant_text(q: int) -> str:
    """格式化数量显示"""
    if q >= 10000000:
        return f"{q // 10000000}kw"
    elif q >= 10000:
        x, y = q // 10000, (q % 10000) // 1000
        if x < 10 and y > 0:
            return f"{x}w{y}"
        return f"{x}w"
    elif q >= 1000:
        x, y = q // 1000, (q % 1000) // 100
        if x < 10 and y > 0:
            return f"{x}k{y}"
        return f"{x}k"
    else:
        return str(q)


async def compose_area_item_upgrade_materials_image(rqd: AreaItemUpgradeMaterialsRequest) -> Image.Image:
    """合成区域道具升级材料图片

    Args:
        rqd: 区域道具升级材料请求数据

    Returns:
        生成的区域道具升级材料图片
    """
    profile = rqd.profile
    area_items = rqd.area_items
    has_profile = rqd.has_profile

    gray_color, red_color, green_color = (50, 50, 50), (200, 0, 0), (0, 200, 0)
    ok_color = green_color if has_profile else gray_color
    no_color = red_color if has_profile else gray_color

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            if profile:
                await get_detailed_profile_card(profile)

            with (
                HSplit().set_content_align("lt").set_item_align("lt").set_sep(16).set_bg(roundrect_bg()).set_padding(8)
            ):
                for item in area_items:
                    current_lv = item.current_level

                    # 每个道具的列
                    with (
                        VSplit()
                        .set_content_align("l")
                        .set_item_align("l")
                        .set_sep(8)
                        .set_item_bg(roundrect_bg())
                        .set_padding(8)
                    ):
                        # 列头
                        item_icon = (
                            await get_img_from_path(ASSETS_BASE_DIR, item.item_icon_path)
                            if item.item_icon_path
                            else None
                        )
                        target_icon = (
                            await get_img_from_path(ASSETS_BASE_DIR, item.target_icon_path)
                            if item.target_icon_path
                            else None
                        )

                        with HSplit().set_content_align("c").set_item_align("c").set_omit_parent_bg(True):
                            if target_icon:
                                ImageBox(target_icon, size=(None, 64))
                            if item_icon:
                                ImageBox(item_icon, size=(128, 64), image_size_mode="fit").set_content_align("c")
                            if current_lv:
                                TextBox(
                                    f"Lv.{current_lv}", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=gray_color)
                                )

                        lv_can_upgrade = True
                        for level_info in item.levels:
                            lv = level_info.level

                            if lv > current_lv:
                                lv_can_upgrade = lv_can_upgrade and level_info.can_upgrade

                            # 列项
                            with HSplit().set_content_align("l").set_item_align("l").set_sep(8).set_padding(8):
                                bonus_text = f"+{level_info.bonus:.1f}%"
                                with VSplit().set_content_align("c").set_item_align("c").set_sep(4):
                                    color = ok_color if lv_can_upgrade else no_color
                                    if lv <= current_lv:
                                        color = gray_color
                                    TextBox(f"{lv}", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=color))
                                    TextBox(
                                        bonus_text, TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=gray_color)
                                    ).set_w(64)

                                if lv <= current_lv:
                                    with VSplit().set_content_align("c").set_item_align("c").set_sep(4):
                                        Spacer(w=64, h=64)
                                        TextBox(" ", TextStyle(font=DEFAULT_BOLD_FONT, size=15, color=gray_color))
                                else:
                                    for mat in level_info.materials:
                                        material_icon = await get_img_from_path(ASSETS_BASE_DIR, mat.material_icon_path)
                                        with VSplit().set_content_align("c").set_item_align("c").set_sep(4):
                                            quantity_text = _get_quant_text(mat.quantity)
                                            have_text = _get_quant_text(mat.have_quantity)
                                            sum_text = _get_quant_text(mat.sum_quantity)
                                            with Frame():
                                                sz = 64
                                                if material_icon:
                                                    ImageBox(material_icon, size=(sz, sz))
                                                TextBox(
                                                    f"x{quantity_text}",
                                                    TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(50, 50, 50)),
                                                ).set_offset((sz, sz)).set_offset_anchor("rb")
                                            color = ok_color if mat.is_enough else no_color
                                            text = f"{have_text}/{sum_text}" if has_profile else f"{sum_text}"
                                            TextBox(text, TextStyle(font=DEFAULT_BOLD_FONT, size=15, color=color))

    add_watermark(canvas)
    return await canvas.get_img()


# ========== 羁绊等级 ==========


async def compose_bonds_image(rqd: BondsRequest) -> Image.Image:
    """合成羁绊等级图片

    Args:
        rqd: 羁绊等级请求数据

    Returns:
        生成的羁绊等级图片
    """
    profile = rqd.profile
    bonds = rqd.bonds
    max_level = rqd.max_level

    header_h, row_h = 56, 48
    header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(25, 25, 25, 255))
    text_style = TextStyle(font=DEFAULT_FONT, size=20, color=(50, 50, 50, 255))
    w1, w2, w3, w4, w5 = 100, 120, 100, 350, 150

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            await get_detailed_profile_card(profile)

            with VSplit().set_content_align("l").set_item_align("l").set_sep(8).set_padding(16).set_bg(roundrect_bg()):
                # 标题
                with (
                    HSplit()
                    .set_content_align("c")
                    .set_item_align("c")
                    .set_sep(8)
                    .set_h(header_h)
                    .set_padding(4)
                    .set_bg(roundrect_bg())
                ):
                    TextBox("角色", header_style).set_w(w1).set_content_align("c")
                    TextBox("角色等级", header_style).set_w(w2).set_content_align("c")
                    TextBox("羁绊等级", header_style).set_w(w3).set_content_align("c")
                    TextBox(f"进度(上限{max_level}级)", header_style).set_w(w4).set_content_align("c")
                    TextBox("升级经验", header_style).set_w(w5).set_content_align("c")

                # 项目
                for idx, bond in enumerate(bonds):
                    bg_color = (255, 255, 255, 150) if idx % 2 == 0 else (255, 255, 255, 100)

                    level = bond.bond_level
                    level_text = str(level) if level else "-"

                    if not bond.has_bond:
                        need_exp_text = "-"
                    elif level == max_level:
                        need_exp_text = "MAX"
                    elif bond.need_exp is not None:
                        need_exp_text = str(bond.need_exp)
                    else:
                        need_exp_text = "-"

                    chara_rank_text = f"{bond.chara_rank1} & {bond.chara_rank2}"

                    level_color = (50, 50, 50, 255)
                    if min(bond.chara_rank1, bond.chara_rank2) <= level < max_level:
                        level_color = (150, 0, 0, 255)

                    chara_icon1 = await get_img_from_path(ASSETS_BASE_DIR, bond.chara_icon_path1)
                    chara_icon2 = await get_img_from_path(ASSETS_BASE_DIR, bond.chara_icon_path2)

                    with (
                        HSplit()
                        .set_content_align("c")
                        .set_item_align("c")
                        .set_sep(8)
                        .set_h(row_h)
                        .set_padding(4)
                        .set_bg(roundrect_bg(fill=bg_color))
                    ):
                        with Frame().set_w(w1).set_content_align("c"):
                            if chara_icon1:
                                ImageBox(chara_icon1, size=(None, 40)).set_offset((-13, 0))
                            if chara_icon2:
                                ImageBox(chara_icon2, size=(None, 40)).set_offset((13, 0))

                        TextBox(chara_rank_text, text_style.replace(font=DEFAULT_BOLD_FONT, color=level_color)).set_w(
                            w2
                        ).set_content_align("c")
                        TextBox(level_text, text_style.replace(font=DEFAULT_BOLD_FONT, color=level_color)).set_w(
                            w3
                        ).set_content_align("c")

                        with Frame().set_w(w4).set_content_align("lt"):
                            progress = max(min(level / max_level, 1), 0) if max_level > 0 else 0
                            total_w, total_h, border = w4, 14, 2
                            progress_w = int((total_w - border * 2) * progress)
                            progress_h = total_h - border * 2
                            color = LinearGradient(c1=bond.color1, c2=bond.color2, p1=(0, 0.5), p2=(1, 0.5))

                            if bond.has_bond and progress > 0:
                                Spacer(w=total_w, h=total_h).set_bg(
                                    RoundRectBg(fill=(100, 100, 100, 255), radius=total_h // 2)
                                )
                                Spacer(w=progress_w, h=progress_h).set_bg(
                                    RoundRectBg(fill=color, radius=(total_h - border) // 2)
                                ).set_offset((border, border))

                                def draw_line(line_x: int):
                                    p = line_x / max_level if max_level > 0 else 0
                                    if p <= 0 or p >= 1:
                                        return
                                    lx = int((total_w - border * 2) * p)
                                    line_color = (100, 100, 100, 255) if line_x < level else (150, 150, 150, 255)
                                    Spacer(w=1, h=total_h // 2 - 1).set_bg(FillBg(line_color)).set_offset(
                                        (border + lx - 1, total_h // 2)
                                    )

                                for line_x in range(0, max_level, 10):
                                    draw_line(line_x)
                            else:
                                Spacer(w=total_w, h=total_h).set_bg(
                                    RoundRectBg(fill=(100, 100, 100, 100), radius=total_h // 2)
                                )

                        TextBox(need_exp_text, text_style).set_w(w5).set_content_align("c")

    add_watermark(canvas)
    return await canvas.get_img()


# ========== 队长次数 ==========


async def compose_leader_count_image(rqd: LeaderCountRequest) -> Image.Image:
    """合成队长次数图片

    Args:
        rqd: 队长次数请求数据

    Returns:
        生成的队长次数图片
    """
    profile = rqd.profile
    leader_counts = rqd.leader_counts
    max_play_count = rqd.max_play_count

    header_h, row_h = 56, 48
    header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(25, 25, 25, 255))
    text_style = TextStyle(font=DEFAULT_FONT, size=20, color=(50, 50, 50, 255))
    w1, w2, w3, w4, w5 = 80, 100, 100, 100, 350

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            await get_detailed_profile_card(profile)

            with VSplit().set_content_align("l").set_item_align("l").set_sep(8).set_padding(16).set_bg(roundrect_bg()):
                # 标题
                with (
                    HSplit()
                    .set_content_align("c")
                    .set_item_align("c")
                    .set_sep(8)
                    .set_h(header_h)
                    .set_padding(4)
                    .set_bg(roundrect_bg())
                ):
                    TextBox("角色", header_style).set_w(w1).set_content_align("c")
                    TextBox("队长次数", header_style).set_w(w2).set_content_align("c")
                    TextBox("EX等级", header_style).set_w(w3).set_content_align("c")
                    TextBox("EX次数", header_style).set_w(w4).set_content_align("c")
                    TextBox(f"进度(上限{max_play_count})", header_style).set_w(w5).set_content_align("c")

                # 项目
                for idx, info in enumerate(leader_counts):
                    bg_color = (255, 255, 255, 150) if idx % 2 == 0 else (255, 255, 255, 100)

                    pc = info.play_count
                    pc_text = str(pc) if pc else "-"
                    pc_ex_text = str(info.ex_count) if info.ex_count else "-"
                    ex_level_text = f"x{info.ex_level}" if info.ex_level else "-"

                    chara_icon = await get_img_from_path(ASSETS_BASE_DIR, info.chara_icon_path)

                    with (
                        HSplit()
                        .set_content_align("c")
                        .set_item_align("c")
                        .set_sep(8)
                        .set_h(row_h)
                        .set_padding(4)
                        .set_bg(roundrect_bg(fill=bg_color))
                    ):
                        with Frame().set_w(w1).set_content_align("c"):
                            if chara_icon:
                                ImageBox(chara_icon, size=(None, 40))

                        TextBox(pc_text, text_style.replace(font=DEFAULT_BOLD_FONT)).set_w(w2).set_content_align("c")
                        TextBox(ex_level_text, text_style.replace(font=DEFAULT_BOLD_FONT)).set_w(w3).set_content_align(
                            "c"
                        )
                        TextBox(pc_ex_text, text_style.replace(font=DEFAULT_BOLD_FONT)).set_w(w4).set_content_align("c")

                        with Frame().set_w(w5).set_content_align("lt"):
                            progress = max(min(pc / max_play_count, 1), 0) if max_play_count > 0 else 0
                            total_w, total_h, border = w5, 14, 2
                            progress_w = int((total_w - border * 2) * progress)
                            progress_h = total_h - border * 2

                            color = (255, 50, 50, 255)
                            if pc > 50000:
                                color = (100, 255, 100, 255)
                            elif pc > 40000:
                                color = (255, 255, 100, 255)
                            elif pc > 30000:
                                color = (255, 200, 100, 255)
                            elif pc > 20000:
                                color = (255, 150, 100, 255)
                            elif pc > 10000:
                                color = (255, 100, 100, 255)

                            if progress > 0:
                                Spacer(w=total_w, h=total_h).set_bg(
                                    RoundRectBg(fill=(100, 100, 100, 255), radius=total_h // 2)
                                )
                                Spacer(w=progress_w, h=progress_h).set_bg(
                                    RoundRectBg(fill=color, radius=(total_h - border) // 2)
                                ).set_offset((border, border))

                                def draw_line(line_x: int):
                                    p = line_x / max_play_count if max_play_count > 0 else 0
                                    if p <= 0 or p >= 1:
                                        return
                                    lx = int((total_w - border * 2) * p)
                                    line_color = (100, 100, 100, 255) if line_x < pc else (150, 150, 150, 255)
                                    Spacer(w=1, h=total_h // 2 - 1).set_bg(FillBg(line_color)).set_offset(
                                        (border + lx - 1, total_h // 2)
                                    )

                                for line_x in range(0, max_play_count, 10000):
                                    draw_line(line_x)
                            else:
                                Spacer(w=total_w, h=total_h).set_bg(
                                    RoundRectBg(fill=(100, 100, 100, 100), radius=total_h // 2)
                                )

    add_watermark(canvas)
    return await canvas.get_img()
