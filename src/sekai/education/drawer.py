"""
Education 模块绘图函数

提供挑战Live详情、加成详情、区域道具升级材料、羁绊等级、队长次数等图片的绘制功能。
"""

import asyncio
import logging
import math
import time

from PIL import Image

from src.core.heavy_render_pool import EncodedImagePayload
from src.sekai.base.draw import (
    BG_PADDING,
    SEKAI_BLUE_BG,
    Canvas,
    TextStyle,
    add_request_watermark,
    roundrect_bg,
)
from src.sekai.base.painter import BLACK
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
    Widget,
)
from src.sekai.base.utils import get_img_from_path
from src.sekai.profile.drawer import get_profile_card
from src.sekai.skia_renderer.canvas import render_canvas_payload, skia_plot_enabled
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT

# 从 model.py 导入数据模型
from .model import (
    AreaItemUpgradeMaterialsRequest,
    BondsRequest,
    ChallengeLiveDetailsRequest,
    CharacterMissionAllRequest,
    CharacterMissionOverviewRequest,
    LeaderCountRequest,
    PowerBonusDetailRequest,
)

logger = logging.getLogger(__name__)

BONUS_ICON_SLOT_W = 44
BONUS_ICON_SLOT_H = 40
INFO_PANEL_ROW_ALPHA = 80
INFO_PANEL_ROW_ALT_ALPHA = 64
CHARACTER_MISSION_PANEL_ALPHA = 80
CHARACTER_MISSION_CARD_ALPHA = 80


def _info_panel_row_fill(idx: int) -> tuple[int, int, int, int]:
    alpha = INFO_PANEL_ROW_ALPHA if idx % 2 == 0 else INFO_PANEL_ROW_ALT_ALPHA
    return (255, 255, 255, alpha)


def _character_mission_panel_bg() -> RoundRectBg:
    return roundrect_bg(alpha=CHARACTER_MISSION_PANEL_ALPHA)


def _character_mission_card_bg() -> RoundRectBg:
    return roundrect_bg(alpha=CHARACTER_MISSION_CARD_ALPHA)


# ========== 挑战Live详情 ==========


async def _build_challenge_live_detail_canvas(rqd: ChallengeLiveDetailsRequest) -> Canvas:
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

    # 获取图标（并行）
    _icon_tasks = []
    if rqd.jewel_icon_path:
        _icon_tasks.append(get_img_from_path(ASSETS_BASE_DIR, rqd.jewel_icon_path))
    if rqd.shard_icon_path:
        _icon_tasks.append(get_img_from_path(ASSETS_BASE_DIR, rqd.shard_icon_path))
    _icon_results = await asyncio.gather(*_icon_tasks) if _icon_tasks else []
    _idx = 0
    if rqd.jewel_icon_path:
        jewel_icon = _icon_results[_idx]
        _idx += 1
    else:
        jewel_icon = None
    if rqd.shard_icon_path:
        shard_icon = _icon_results[_idx]
    else:
        shard_icon = None

    # 预加载角色图标（并行）
    _t0 = time.perf_counter()
    chara_icons = await asyncio.gather(
        *[get_img_from_path(ASSETS_BASE_DIR, ch.chara_icon_path) for ch in character_challenges]
    )
    logger.debug(
        "[perf] compose_challenge_live_detail_image preload %d chara icons: %.3fs",
        len(chara_icons),
        time.perf_counter() - _t0,
    )

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            await get_profile_card(profile.to_profile_card_request())

            with (
                VSplit()
                .set_content_align("c")
                .set_item_align("c")
                .set_sep(8)
                .set_padding(16)
                .set_bg(roundrect_bg(alpha=80))
            ):
                # 标题行
                with (
                    HSplit()
                    .set_content_align("c")
                    .set_item_align("c")
                    .set_sep(8)
                    .set_h(header_h)
                    .set_padding(4)
                    .set_bg(roundrect_bg(alpha=80))
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
                    bg_color = _info_panel_row_fill(idx)

                    rank_text = str(challenge.rank) if challenge.rank else "-"
                    score_text = str(challenge.score) if challenge.score else "-"
                    jewel_text = str(challenge.jewel)
                    shard_text = str(challenge.shard)

                    chara_icon = chara_icons[idx]

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

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_challenge_live_detail_image(rqd: ChallengeLiveDetailsRequest) -> Image.Image:
    return await (await _build_challenge_live_detail_canvas(rqd)).get_img()


async def try_render_challenge_live_detail_payload(
    rqd: ChallengeLiveDetailsRequest,
) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_challenge_live_detail_canvas(rqd))


# ========== 加成详情 ==========


async def _build_power_bonus_detail_canvas(rqd: PowerBonusDetailRequest) -> Canvas:
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

    # 预加载所有图标（并行）
    _t0 = time.perf_counter()
    _chara_icon_imgs = await asyncio.gather(
        *[get_img_from_path(ASSETS_BASE_DIR, b.chara_icon_path) for b in chara_bonuses]
    )
    _unit_icon_imgs = await asyncio.gather(
        *[get_img_from_path(ASSETS_BASE_DIR, b.unit_icon_path) for b in unit_bonuses]
    )
    _attr_icon_imgs = await asyncio.gather(
        *[get_img_from_path(ASSETS_BASE_DIR, b.attr_icon_path) for b in attr_bonuses]
    )
    logger.debug(
        "[perf] compose_power_bonus_detail_image preload %d icons: %.3fs",
        len(chara_bonuses) + len(unit_bonuses) + len(attr_bonuses),
        time.perf_counter() - _t0,
    )

    def draw_bonus_icon(icon: Image.Image | None) -> None:
        with Frame().set_size((BONUS_ICON_SLOT_W, BONUS_ICON_SLOT_H)).set_content_align("c"):
            if icon:
                ImageBox(icon, size=(40, 40), image_size_mode="fit")
            else:
                Spacer(w=BONUS_ICON_SLOT_W, h=BONUS_ICON_SLOT_H)

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            await get_profile_card(profile.to_profile_card_request())

            with (
                VSplit()
                .set_content_align("lt")
                .set_item_align("lt")
                .set_sep(8)
                .set_item_bg(roundrect_bg(alpha=80))
                .set_bg(roundrect_bg(alpha=80))
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
                            chara_icon = _chara_icon_imgs[chara_bonuses.index(bonus)]
                            with HSplit().set_content_align("l").set_item_align("c").set_sep(4):
                                draw_bonus_icon(chara_icon)
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
                    for i, bonus in enumerate(unit_bonuses):
                        unit_icon = _unit_icon_imgs[i]
                        with HSplit().set_content_align("l").set_item_align("c").set_sep(4):
                            draw_bonus_icon(unit_icon)
                            TextBox(f"{bonus.total:.1f}%", header_style).set_w(100).set_content_align("r").set_overflow(
                                "clip"
                            )
                            detail = f"区域道具{bonus.area_item:.1f}% + 烤森门{bonus.gate:.1f}%"
                            TextBox(detail, text_style)

                # 属性加成
                with Grid(col_count=5).set_content_align("l").set_item_align("l").set_sep(20, 4).set_padding(16):
                    for i, bonus in enumerate(attr_bonuses):
                        attr_icon = _attr_icon_imgs[i]
                        with HSplit().set_content_align("l").set_item_align("c").set_sep(4):
                            draw_bonus_icon(attr_icon)
                            TextBox(f"{bonus.total:.1f}%", header_style).set_w(100).set_content_align("r").set_overflow(
                                "clip"
                            )

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_power_bonus_detail_image(rqd: PowerBonusDetailRequest) -> Image.Image:
    return await (await _build_power_bonus_detail_canvas(rqd)).get_img()


async def try_render_power_bonus_detail_payload(
    rqd: PowerBonusDetailRequest,
) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_power_bonus_detail_canvas(rqd))


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


async def _build_area_item_upgrade_materials_canvas(rqd: AreaItemUpgradeMaterialsRequest) -> Canvas:
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

    # 预加载所有图标（并行）
    _all_icon_paths = {}
    for item in area_items:
        if item.item_icon_path:
            _all_icon_paths[item.item_icon_path] = None
        if item.target_icon_path:
            _all_icon_paths[item.target_icon_path] = None
        for level_info in item.levels:
            for mat in level_info.materials:
                _all_icon_paths[mat.material_icon_path] = None
    _unique_paths = list(_all_icon_paths.keys())
    if _unique_paths:
        _t0 = time.perf_counter()
        _loaded = await asyncio.gather(*[get_img_from_path(ASSETS_BASE_DIR, p) for p in _unique_paths])
        logger.debug(
            "[perf] compose_area_item_upgrade_materials_image preload %d icons: %.3fs",
            len(_unique_paths),
            time.perf_counter() - _t0,
        )
        _icon_cache = dict(zip(_unique_paths, _loaded))
    else:
        _icon_cache = {}

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            if profile:
                await get_profile_card(profile.to_profile_card_request())

            with (
                HSplit()
                .set_content_align("lt")
                .set_item_align("lt")
                .set_sep(16)
                .set_bg(roundrect_bg(alpha=80))
                .set_padding(8)
            ):
                for item in area_items:
                    current_lv = item.current_level

                    # 每个道具的列
                    with (
                        VSplit()
                        .set_content_align("l")
                        .set_item_align("l")
                        .set_sep(8)
                        .set_item_bg(roundrect_bg(alpha=80))
                        .set_padding(8)
                    ):
                        # 列头
                        item_icon = _icon_cache.get(item.item_icon_path) if item.item_icon_path else None
                        target_icon = _icon_cache.get(item.target_icon_path) if item.target_icon_path else None

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
                                        material_icon = _icon_cache.get(mat.material_icon_path)
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

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_area_item_upgrade_materials_image(rqd: AreaItemUpgradeMaterialsRequest) -> Image.Image:
    return await (await _build_area_item_upgrade_materials_canvas(rqd)).get_img()


async def try_render_area_item_upgrade_materials_payload(
    rqd: AreaItemUpgradeMaterialsRequest,
) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_area_item_upgrade_materials_canvas(rqd))


# ========== 羁绊等级 ==========


async def _build_bonds_canvas(rqd: BondsRequest) -> Canvas:
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

    # 预加载所有角色图标（并行）
    _bond_icon_tasks = []
    for bond in bonds:
        _bond_icon_tasks.append(get_img_from_path(ASSETS_BASE_DIR, bond.chara_icon_path1))
        _bond_icon_tasks.append(get_img_from_path(ASSETS_BASE_DIR, bond.chara_icon_path2))
    _t0 = time.perf_counter()
    _bond_icons = await asyncio.gather(*_bond_icon_tasks) if _bond_icon_tasks else []
    logger.debug(
        "[perf] compose_bonds_image preload %d bond icons: %.3fs",
        len(_bond_icon_tasks),
        time.perf_counter() - _t0,
    )

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            await get_profile_card(profile.to_profile_card_request())

            with (
                VSplit()
                .set_content_align("l")
                .set_item_align("l")
                .set_sep(8)
                .set_padding(16)
                .set_bg(roundrect_bg(alpha=80))
            ):
                # 标题
                with (
                    HSplit()
                    .set_content_align("c")
                    .set_item_align("c")
                    .set_sep(8)
                    .set_h(header_h)
                    .set_padding(4)
                    .set_bg(roundrect_bg(alpha=80))
                ):
                    TextBox("角色", header_style).set_w(w1).set_content_align("c")
                    TextBox("角色等级", header_style).set_w(w2).set_content_align("c")
                    TextBox("羁绊等级", header_style).set_w(w3).set_content_align("c")
                    TextBox(f"进度(上限{max_level}级)", header_style).set_w(w4).set_content_align("c")
                    TextBox("升级经验", header_style).set_w(w5).set_content_align("c")

                # 项目
                for idx, bond in enumerate(bonds):
                    bg_color = _info_panel_row_fill(idx)

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

                    chara_icon1 = _bond_icons[idx * 2]
                    chara_icon2 = _bond_icons[idx * 2 + 1]

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

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_bonds_image(rqd: BondsRequest) -> Image.Image:
    return await (await _build_bonds_canvas(rqd)).get_img()


async def try_render_bonds_payload(rqd: BondsRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_bonds_canvas(rqd))


# ========== 队长次数 ==========


async def _build_leader_count_canvas(rqd: LeaderCountRequest) -> Canvas:
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

    # 预加载所有角色图标（并行）
    _t0 = time.perf_counter()
    _leader_icons = await asyncio.gather(
        *[get_img_from_path(ASSETS_BASE_DIR, info.chara_icon_path) for info in leader_counts]
    )
    logger.debug(
        "[perf] compose_leader_count_image preload %d icons: %.3fs",
        len(leader_counts),
        time.perf_counter() - _t0,
    )

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            await get_profile_card(profile.to_profile_card_request())

            with (
                VSplit()
                .set_content_align("l")
                .set_item_align("l")
                .set_sep(8)
                .set_padding(16)
                .set_bg(roundrect_bg(alpha=80))
            ):
                # 标题
                with (
                    HSplit()
                    .set_content_align("c")
                    .set_item_align("c")
                    .set_sep(8)
                    .set_h(header_h)
                    .set_padding(4)
                    .set_bg(roundrect_bg(alpha=80))
                ):
                    TextBox("角色", header_style).set_w(w1).set_content_align("c")
                    TextBox("队长次数", header_style).set_w(w2).set_content_align("c")
                    TextBox("EX等级", header_style).set_w(w3).set_content_align("c")
                    TextBox("EX次数", header_style).set_w(w4).set_content_align("c")
                    TextBox(f"进度(上限{max_play_count})", header_style).set_w(w5).set_content_align("c")

                # 项目
                for idx, info in enumerate(leader_counts):
                    bg_color = _info_panel_row_fill(idx)

                    pc = info.play_count
                    pc_text = str(pc) if pc else "-"
                    pc_ex_text = str(info.ex_count) if info.ex_count else "-"
                    ex_level_text = f"x{info.ex_level}" if info.ex_level else "-"

                    chara_icon = _leader_icons[idx]

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

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_leader_count_image(rqd: LeaderCountRequest) -> Image.Image:
    return await (await _build_leader_count_canvas(rqd)).get_img()


async def try_render_leader_count_payload(rqd: LeaderCountRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_leader_count_canvas(rqd))


def _education_progress_color(ratio: float) -> tuple[int, int, int, int]:
    if ratio >= 1.0:
        return (100, 255, 100, 255)
    if ratio > 0.8:
        return (255, 255, 100, 255)
    if ratio > 0.6:
        return (255, 200, 100, 255)
    if ratio > 0.4:
        return (255, 150, 100, 255)
    if ratio > 0.2:
        return (255, 100, 100, 255)
    return (255, 50, 50, 255)


def _draw_character_mission_progress(
    title: str,
    current: int,
    upper: int | None,
    ratio: float,
    bar_width: int,
    *,
    next_need: int | None = None,
    next_exp: int | None = None,
    title_badge: str | None = None,
) -> Widget:
    title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(35, 35, 35, 255))
    text_style = TextStyle(font=DEFAULT_FONT, size=15, color=(55, 55, 55, 255))
    root = VSplit().set_content_align("l").set_item_align("l").set_sep(8)

    title_row = HSplit().set_content_align("c").set_item_align("c").set_sep(8)
    title_row.add_item(TextBox(title, title_style))
    if title_badge:
        title_row.add_item(
            TextBox(title_badge, TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(55, 55, 55, 255)))
            .set_bg(roundrect_bg(fill=(255, 255, 255, 180), radius=8))
            .set_padding((8, 2))
        )
    title_frame = Frame().set_w(bar_width).set_content_align("c")
    title_frame.add_item(title_row)
    root.add_item(title_frame)

    bar = Frame().set_w(bar_width).set_h(18).set_content_align("lt")
    progress = max(0.0, min(ratio, 1.0))
    total_w, total_h, border = bar_width, 14, 2
    progress_w = int((total_w - border * 2) * progress)
    progress_h = total_h - border * 2

    if progress > 0:
        bar.add_item(Spacer(w=total_w, h=total_h).set_bg(RoundRectBg(fill=(100, 100, 100, 255), radius=total_h // 2)))
        bar.add_item(
            Spacer(w=progress_w, h=progress_h)
            .set_bg(RoundRectBg(fill=_education_progress_color(progress), radius=(total_h - border) // 2))
            .set_offset((border, border))
        )
        for i in range(1, 5):
            lx = int((total_w - border * 2) * (i / 5.0))
            line_color = (100, 100, 100, 255) if i / 5.0 < progress else (150, 150, 150, 255)
            bar.add_item(
                Spacer(w=1, h=total_h // 2 - 1).set_bg(FillBg(line_color)).set_offset((border + lx - 1, total_h // 2))
            )
    else:
        bar.add_item(Spacer(w=total_w, h=total_h).set_bg(RoundRectBg(fill=(100, 100, 100, 100), radius=total_h // 2)))
    root.add_item(bar)

    upper_text = "∞" if upper is None else f"{upper:,}"
    pct_text = "-" if upper is None or upper <= 0 else f"{min(current / upper * 100, 100.0):.1f}%"
    info_row = HSplit().set_content_align("c").set_item_align("c").set_sep(8)
    info_row.add_item(TextBox(f"{current:,}/{upper_text} ({pct_text})", text_style))
    if next_need is not None:
        exp_text = "?" if next_exp is None else str(next_exp)
        info_row.add_item(
            TextBox(
                f"下一档{current:,}/{next_need:,} EXP+{exp_text}",
                TextStyle(font=DEFAULT_FONT, size=14, color=(80, 80, 80, 255)),
            )
        )
    else:
        info_row.add_item(TextBox("下一档已满", TextStyle(font=DEFAULT_FONT, size=14, color=(80, 80, 80, 255))))
    root.add_item(info_row)
    return root


def _build_character_mission_card(row, card_w: int) -> Widget:
    frame = Frame().set_w(card_w).set_bg(_character_mission_card_bg()).set_padding((12, 10))
    frame.add_item(
        _draw_character_mission_progress(
            row.title,
            row.current,
            row.upper,
            row.ratio,
            card_w - 24,
            next_need=row.next_need,
            next_exp=row.next_exp,
            title_badge=row.ex_display_round_text,
        )
    )
    return frame


def _build_character_mission_dual_card(
    title: str,
    normal_row,
    ex_row,
    card_w: int,
) -> Widget:
    frame = Frame().set_w(card_w).set_bg(_character_mission_card_bg()).set_padding((12, 10))
    content = VSplit().set_content_align("l").set_item_align("l").set_sep(10)

    title_frame = Frame().set_w(card_w - 24).set_content_align("c")
    title_row = HSplit().set_content_align("c").set_item_align("c").set_sep(8)
    title_row.add_item(TextBox(title, TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(20, 20, 20, 255))))
    title_frame.add_item(title_row)
    content.add_item(title_frame)

    content.add_item(
        _draw_character_mission_progress(
            "普通任务",
            normal_row.current,
            normal_row.upper,
            normal_row.ratio,
            card_w - 24,
            next_need=normal_row.next_need,
            next_exp=normal_row.next_exp,
        )
    )
    content.add_item(
        _draw_character_mission_progress(
            "EX任务",
            ex_row.current,
            ex_row.upper,
            ex_row.ratio,
            card_w - 24,
            next_need=ex_row.next_need,
            next_exp=ex_row.next_exp,
            title_badge=ex_row.ex_display_round_text,
        )
    )
    frame.add_item(content)
    return frame


async def _build_character_mission_overview_canvas(rqd: CharacterMissionOverviewRequest) -> Canvas:
    chara_icon = await get_img_from_path(ASSETS_BASE_DIR, rqd.character_icon_path)
    header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(25, 25, 25, 255))
    sub_header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(35, 35, 35, 255))
    note_style = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(0, 0, 0, 255))
    card_w = 520

    canvas = Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING)
    root = VSplit().set_content_align("lt").set_item_align("lt").set_sep(16)
    root.add_item(await get_profile_card(rqd.profile.to_profile_card_request()))

    note_panel = (
        VSplit()
        .set_content_align("l")
        .set_item_align("l")
        .set_sep(8)
        .set_padding(12)
        .set_bg(_character_mission_panel_bg())
    )
    note_panel.add_item(
        TextBox(
            "各任务上限为MasterData中所规定的上限，并不一定是当前已实装资源总数",
            note_style,
            use_real_line_count=True,
        )
    )
    root.add_item(note_panel)

    summary_panel = (
        VSplit()
        .set_bg(_character_mission_panel_bg())
        .set_padding(16)
        .set_sep(12)
        .set_content_align("lt")
        .set_item_align("lt")
    )
    header_row = HSplit().set_content_align("c").set_item_align("c").set_sep(12)
    header_row.add_item(ImageBox(chara_icon, size=(48, 48)))
    header_row.add_item(
        TextBox(
            f"{rqd.character_name} 当前Lv.{rqd.current_level} EXP×{rqd.current_exp} + "
            f"未领取EXP×{rqd.pending_exp} = 总计Lv.{rqd.final_level} EXP×{rqd.final_exp}",
            header_style,
            use_real_line_count=True,
        )
    )
    summary_panel.add_item(header_row)
    root.add_item(summary_panel)

    basic_panel = (
        VSplit()
        .set_bg(_character_mission_panel_bg())
        .set_padding(16)
        .set_sep(12)
        .set_content_align("lt")
        .set_item_align("lt")
    )
    basic_panel.add_item(TextBox("基本任务", sub_header_style))
    if rqd.basic_rows:
        for i in range(0, len(rqd.basic_rows), 2):
            row = HSplit().set_content_align("lt").set_item_align("lt").set_sep(16)
            row.add_item(_build_character_mission_card(rqd.basic_rows[i], card_w))
            if i + 1 < len(rqd.basic_rows):
                row.add_item(_build_character_mission_card(rqd.basic_rows[i + 1], card_w))
            else:
                row.add_item(Spacer(card_w, 1))
            basic_panel.add_item(row)
    else:
        basic_panel.add_item(
            TextBox("暂无可显示的基本任务", TextStyle(font=DEFAULT_FONT, size=18, color=(80, 80, 80, 255)))
        )
    root.add_item(basic_panel)

    achievement_panel = (
        VSplit()
        .set_bg(_character_mission_panel_bg())
        .set_padding(16)
        .set_sep(12)
        .set_content_align("lt")
        .set_item_align("lt")
    )
    achievement_panel.add_item(TextBox("成就", sub_header_style))
    by_type = {row.mission_type: row for row in rqd.achievement_rows}

    play_live = by_type.get("play_live")
    play_live_ex = by_type.get("play_live_ex")
    waiting_room = by_type.get("waiting_room")
    waiting_room_ex = by_type.get("waiting_room_ex")
    if play_live and play_live_ex and waiting_room and waiting_room_ex:
        dual_row = HSplit().set_content_align("lt").set_item_align("lt").set_sep(16)
        dual_row.add_item(_build_character_mission_dual_card("队长次数", play_live, play_live_ex, card_w))
        dual_row.add_item(_build_character_mission_dual_card("休息室次数", waiting_room, waiting_room_ex, card_w))
        achievement_panel.add_item(dual_row)

    remaining_rows = [
        row
        for row in rqd.achievement_rows
        if row.mission_type not in {"play_live", "play_live_ex", "waiting_room", "waiting_room_ex"}
    ]
    if remaining_rows:
        for i in range(0, len(remaining_rows), 2):
            row = HSplit().set_content_align("lt").set_item_align("lt").set_sep(16)
            row.add_item(_build_character_mission_card(remaining_rows[i], card_w))
            if i + 1 < len(remaining_rows):
                row.add_item(_build_character_mission_card(remaining_rows[i + 1], card_w))
            else:
                row.add_item(Spacer(card_w, 1))
            achievement_panel.add_item(row)
    elif not (play_live and play_live_ex and waiting_room and waiting_room_ex):
        achievement_panel.add_item(
            TextBox("暂无可显示的成就任务", TextStyle(font=DEFAULT_FONT, size=18, color=(80, 80, 80, 255)))
        )
    root.add_item(achievement_panel)

    canvas.add_item(root)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_character_mission_overview_image(rqd: CharacterMissionOverviewRequest) -> Image.Image:
    return await (await _build_character_mission_overview_canvas(rqd)).get_img()


async def try_render_character_mission_overview_payload(
    rqd: CharacterMissionOverviewRequest,
) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_character_mission_overview_canvas(rqd))


async def _build_character_mission_all_canvas(rqd: CharacterMissionAllRequest) -> Canvas:
    chara_icon = await get_img_from_path(ASSETS_BASE_DIR, rqd.character_icon_path)
    title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=26, color=BLACK)
    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=BLACK)
    style2 = TextStyle(font=DEFAULT_FONT, size=20, color=(50, 50, 50))
    style3 = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(200, 50, 50))
    sub_header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=(35, 35, 35, 255))
    gh, vsep, hsep = 40, 6, 6
    gw_seq, gw_req, gw_acc_req, gw_exp, gw_acc_exp = 84, 96, 128, 72, 116
    default_chunk_size = 40

    def draw_section_table(section, target_col_count: int | None = None) -> Widget:
        root = (
            VSplit()
            .set_content_align("lt")
            .set_item_align("lt")
            .set_sep(8)
            .set_padding(8)
            .set_bg(_character_mission_panel_bg())
        )
        root.add_item(TextBox("EX任务" if section.is_ex else "普通任务", sub_header_style))

        header_row = HSplit().set_content_align("lb").set_item_align("lb").set_sep(8)
        header_row.add_item(TextBox("当前进度:", style1))
        header_row.add_item(TextBox(str(section.current_total), style3))
        if section.is_ex and section.current_round_no is not None:
            header_row.add_item(TextBox(f"当前回目 EX {section.current_round_no}", style2))
        elif section.reached_seq > 0:
            header_row.add_item(TextBox(f"已达档位 #{section.reached_seq}", style2))
        root.add_item(header_row)

        root.add_item(
            _draw_character_mission_progress(
                "",
                section.current_total,
                section.upper,
                section.ratio,
                560,
                next_need=section.next_need,
                next_exp=section.next_exp,
            )
        )

        if target_col_count and target_col_count > 1:
            chunk_size = max(1, math.ceil(len(section.display_rows) / target_col_count))
        else:
            chunk_size = default_chunk_size
        chunks = [
            section.display_rows[i : i + chunk_size] for i in range(0, len(section.display_rows), chunk_size)
        ] or [[]]
        column_wrap = HSplit().set_content_align("lt").set_item_align("lt").set_sep(12)
        for chunk in chunks:
            grid_row = HSplit().set_content_align("lt").set_item_align("lt").set_sep(hsep)

            def build_col(title: str, width: int, extractor):
                col = VSplit().set_content_align("c").set_item_align("c").set_sep(vsep)
                col.add_item(TextBox(title, style1).set_size((width, gh)).set_content_align("c"))
                for idx, row in enumerate(chunk):
                    bg_fill = roundrect_bg(
                        fill=(
                            (255, 244, 196, 210)
                            if row.seq == section.reached_seq and section.reached_seq > 0
                            else _info_panel_row_fill(idx)
                        )
                    )
                    col.add_item(
                        TextBox(str(extractor(row)), style2)
                        .set_bg(bg_fill)
                        .set_size((width, gh))
                        .set_content_align("c")
                    )
                return col

            grid_row.add_item(build_col("档位", gw_seq, lambda row: f"#{row.seq}"))
            grid_row.add_item(build_col("需求", gw_req, lambda row: row.requirement))
            grid_row.add_item(build_col("累计需求", gw_acc_req, lambda row: row.acc_requirement))
            grid_row.add_item(build_col("EXP", gw_exp, lambda row: row.exp))
            grid_row.add_item(build_col("累计EXP", gw_acc_exp, lambda row: row.acc_exp))
            column_wrap.add_item(grid_row)
        root.add_item(column_wrap)
        return root

    canvas = Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING)
    root = VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_item_bg(_character_mission_panel_bg())
    root.add_item(await get_profile_card(rqd.profile.to_profile_card_request()))

    header = VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_padding(8)
    title_row = HSplit().set_content_align("lb").set_item_align("c").set_sep(8)
    title_row.add_item(ImageBox(chara_icon, size=(48, 48)))
    title_row.add_item(TextBox(f"{rqd.character_name} {rqd.title} 任务详览", title_style))
    header.add_item(title_row)
    header.add_item(TextBox("普通任务高亮栏为已达成的最近档位，EX任务高亮栏为当前进行中的档位", style2))
    root.add_item(header)

    normal_section = next((section for section in rqd.sections if not section.is_ex), None)
    normal_col_count = None
    if normal_section is not None:
        normal_col_count = max(1, math.ceil(len(normal_section.display_rows) / default_chunk_size))

    if rqd.sections:
        for section in rqd.sections:
            target_col_count = normal_col_count if section.is_ex and normal_col_count else None
            root.add_item(draw_section_table(section, target_col_count))
    else:
        empty_panel = (
            VSplit()
            .set_content_align("lt")
            .set_item_align("lt")
            .set_sep(8)
            .set_padding(8)
            .set_bg(_character_mission_panel_bg())
        )
        empty_panel.add_item(TextBox("没有可显示的任务表数据", style2))
        root.add_item(empty_panel)

    canvas.add_item(root)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_character_mission_all_image(rqd: CharacterMissionAllRequest) -> Image.Image:
    return await (await _build_character_mission_all_canvas(rqd)).get_img()


async def try_render_character_mission_all_payload(
    rqd: CharacterMissionAllRequest,
) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_character_mission_all_canvas(rqd))
