"""
Education 模块绘图函数

提供挑战Live详情、加成详情、区域道具升级材料、羁绊等级、队长次数等图片的绘制功能。
"""

import asyncio
import logging
import math
import time

from PIL import Image

from src.core.image_payload import EncodedImagePayload
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
from src.sekai.base.utils import ImageSource, get_asset_image_ref
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


async def _load_asset_refs(paths: list[str], perf_name: str) -> list[ImageSource]:
    started_at = time.perf_counter()
    images = await asyncio.gather(*(get_asset_image_ref(ASSETS_BASE_DIR, path) for path in paths))
    logger.debug("[perf] %s preload %d icons: %.3fs", perf_name, len(paths), time.perf_counter() - started_at)
    return list(images)


async def _load_optional_asset_refs(*paths: str | None) -> list[ImageSource | None]:
    present_paths = [path for path in paths if path]
    loaded = iter(await asyncio.gather(*(get_asset_image_ref(ASSETS_BASE_DIR, path) for path in present_paths)))
    return [next(loaded) if path else None for path in paths]


def _challenge_score_color(score: int) -> tuple[int, int, int, int]:
    if score > 2_500_000:
        return (100, 255, 100, 255)
    if score > 2_000_000:
        return (255, 255, 100, 255)
    if score > 1_500_000:
        return (255, 200, 100, 255)
    if score > 1_000_000:
        return (255, 150, 100, 255)
    if score > 500_000:
        return (255, 100, 100, 255)
    return (255, 50, 50, 255)


def _leader_count_color(play_count: int) -> tuple[int, int, int, int]:
    if play_count > 50_000:
        return (100, 255, 100, 255)
    if play_count > 40_000:
        return (255, 255, 100, 255)
    if play_count > 30_000:
        return (255, 200, 100, 255)
    if play_count > 20_000:
        return (255, 150, 100, 255)
    if play_count > 10_000:
        return (255, 100, 100, 255)
    return (255, 50, 50, 255)


def _build_progress_bar(
    *,
    width: int,
    current: int,
    maximum: int,
    fill,
    tick_step: int,
    enabled: bool = True,
) -> Frame:
    frame = Frame().set_w(width).set_content_align("lt")
    progress = max(min(current / maximum, 1), 0) if maximum > 0 else 0
    total_height, border = 14, 2
    if not enabled or progress <= 0:
        frame.add_item(
            Spacer(w=width, h=total_height).set_bg(RoundRectBg(fill=(100, 100, 100, 100), radius=total_height // 2))
        )
        return frame

    progress_width = int((width - border * 2) * progress)
    progress_height = total_height - border * 2
    frame.add_item(
        Spacer(w=width, h=total_height).set_bg(RoundRectBg(fill=(100, 100, 100, 255), radius=total_height // 2))
    )
    frame.add_item(
        Spacer(w=progress_width, h=progress_height)
        .set_bg(RoundRectBg(fill=fill, radius=(total_height - border) // 2))
        .set_offset((border, border))
    )
    for tick in range(tick_step, maximum, tick_step):
        tick_x = int((width - border * 2) * (tick / maximum))
        line_color = (100, 100, 100, 255) if tick < current else (150, 150, 150, 255)
        frame.add_item(
            Spacer(w=1, h=total_height // 2 - 1)
            .set_bg(FillBg(line_color))
            .set_offset((border + tick_x - 1, total_height // 2))
        )
    return frame


def _build_challenge_live_header(
    max_score: int,
    jewel_icon: ImageSource | None,
    shard_icon: ImageSource | None,
    header_style: TextStyle,
    widths: tuple[int, int, int, int, int, int],
) -> HSplit:
    w1, w2, w3, w4, w5, w6 = widths
    header = (
        HSplit()
        .set_content_align("c")
        .set_item_align("c")
        .set_sep(8)
        .set_h(56)
        .set_padding(4)
        .set_bg(roundrect_bg(alpha=80))
    )
    header.add_item(TextBox("角色", header_style).set_w(w1).set_content_align("c"))
    header.add_item(TextBox("等级", header_style).set_w(w2).set_content_align("c"))
    header.add_item(TextBox("分数", header_style).set_w(w3).set_content_align("c"))
    header.add_item(TextBox(f"进度(上限{max_score // 10000}w)", header_style).set_w(w4).set_content_align("c"))
    for width, icon in ((w5, jewel_icon), (w6, shard_icon)):
        icon_frame = Frame().set_w(width).set_content_align("c")
        if icon:
            icon_frame.add_item(ImageBox(icon, size=(None, 40)))
        header.add_item(icon_frame)
    return header


def _build_challenge_live_row(
    challenge,
    chara_icon: ImageSource | None,
    index: int,
    max_score: int,
    text_style: TextStyle,
    widths: tuple[int, int, int, int, int, int],
) -> HSplit:
    w1, w2, w3, w4, w5, w6 = widths
    row = (
        HSplit()
        .set_content_align("c")
        .set_item_align("c")
        .set_sep(8)
        .set_h(48)
        .set_padding(4)
        .set_bg(roundrect_bg(fill=_info_panel_row_fill(index)))
    )
    icon_frame = Frame().set_w(w1).set_content_align("c")
    if chara_icon:
        icon_frame.add_item(ImageBox(chara_icon, size=(None, 40)))
    row.add_item(icon_frame)

    score = challenge.score or 0
    row.add_item(TextBox(str(challenge.rank) if challenge.rank else "-", text_style).set_w(w2).set_content_align("c"))
    row.add_item(
        TextBox(str(challenge.score) if challenge.score else "-", text_style.replace(font=DEFAULT_BOLD_FONT))
        .set_w(w3)
        .set_content_align("c")
    )
    row.add_item(
        _build_progress_bar(
            width=w4,
            current=score,
            maximum=max_score,
            fill=_challenge_score_color(score),
            tick_step=500_000,
        )
    )
    row.add_item(TextBox(str(challenge.jewel), text_style).set_w(w5).set_content_align("c"))
    row.add_item(TextBox(str(challenge.shard), text_style).set_w(w6).set_content_align("c"))
    return row


# ========== 挑战Live详情 ==========


async def _build_challenge_live_detail_canvas(rqd: ChallengeLiveDetailsRequest) -> Canvas:
    """合成挑战Live详情图片

    Args:
        rqd: 挑战Live详情请求数据

    Returns:
        生成的挑战Live详情图片
    """
    header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(25, 25, 25, 255))
    text_style = TextStyle(font=DEFAULT_FONT, size=20, color=(50, 50, 50, 255))
    widths = (80, 80, 150, 300, 80, 80)
    jewel_icon, shard_icon = await _load_optional_asset_refs(rqd.jewel_icon_path, rqd.shard_icon_path)
    chara_icons = await _load_asset_refs(
        [challenge.chara_icon_path for challenge in rqd.character_challenges],
        "compose_challenge_live_detail_image",
    )

    canvas = Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING)
    root = VSplit().set_content_align("lt").set_item_align("lt").set_sep(16)
    root.add_item(await get_profile_card(rqd.profile.to_profile_card_request()))
    table = (
        VSplit().set_content_align("c").set_item_align("c").set_sep(8).set_padding(16).set_bg(roundrect_bg(alpha=80))
    )
    table.add_item(_build_challenge_live_header(rqd.max_score, jewel_icon, shard_icon, header_style, widths))
    for index, (challenge, chara_icon) in enumerate(zip(rqd.character_challenges, chara_icons, strict=True)):
        table.add_item(_build_challenge_live_row(challenge, chara_icon, index, rqd.max_score, text_style, widths))
    root.add_item(table)
    canvas.add_item(root)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_challenge_live_detail_image(rqd: ChallengeLiveDetailsRequest) -> Image.Image:
    return await (await _build_challenge_live_detail_canvas(rqd)).get_img()


async def try_render_challenge_live_detail_payload(
    rqd: ChallengeLiveDetailsRequest,
) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(
        await _build_challenge_live_detail_canvas(rqd),
        endpoint="education_challenge_live",
    )


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
        *[get_asset_image_ref(ASSETS_BASE_DIR, b.chara_icon_path) for b in chara_bonuses]
    )
    _unit_icon_imgs = await asyncio.gather(
        *[get_asset_image_ref(ASSETS_BASE_DIR, b.unit_icon_path) for b in unit_bonuses]
    )
    _attr_icon_imgs = await asyncio.gather(
        *[get_asset_image_ref(ASSETS_BASE_DIR, b.attr_icon_path) for b in attr_bonuses]
    )
    logger.debug(
        "[perf] compose_power_bonus_detail_image preload %d icons: %.3fs",
        len(chara_bonuses) + len(unit_bonuses) + len(attr_bonuses),
        time.perf_counter() - _t0,
    )

    def draw_bonus_icon(icon: ImageSource | None) -> None:
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
    return await render_canvas_payload(
        await _build_power_bonus_detail_canvas(rqd),
        endpoint="education_power_bonus",
    )


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


def _collect_area_item_icon_paths(area_items) -> list[str]:
    paths: dict[str, None] = {}
    for item in area_items:
        if item.item_icon_path:
            paths[item.item_icon_path] = None
        if item.target_icon_path:
            paths[item.target_icon_path] = None
        for level_info in item.levels:
            for material in level_info.materials:
                paths[material.material_icon_path] = None
    return list(paths)


async def _load_asset_ref_cache(paths: list[str], perf_name: str) -> dict[str, ImageSource]:
    if not paths:
        return {}
    return dict(zip(paths, await _load_asset_refs(paths, perf_name), strict=True))


def _area_level_color(level: int, current_level: int, can_upgrade: bool, has_profile: bool):
    gray_color, red_color, green_color = (50, 50, 50), (200, 0, 0), (0, 200, 0)
    if level <= current_level or not has_profile:
        return gray_color
    return green_color if can_upgrade else red_color


def _build_area_item_header(item, icon_cache: dict[str, ImageSource]) -> HSplit:
    gray_color = (50, 50, 50)
    header = HSplit().set_content_align("c").set_item_align("c").set_omit_parent_bg(True)
    target_icon = icon_cache.get(item.target_icon_path) if item.target_icon_path else None
    item_icon = icon_cache.get(item.item_icon_path) if item.item_icon_path else None
    if target_icon:
        header.add_item(ImageBox(target_icon, size=(None, 64)))
    if item_icon:
        header.add_item(ImageBox(item_icon, size=(128, 64), image_size_mode="fit").set_content_align("c"))
    if item.current_level:
        header.add_item(
            TextBox(f"Lv.{item.current_level}", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=gray_color))
        )
    return header


def _build_completed_area_material_placeholder() -> VSplit:
    gray_color = (50, 50, 50)
    placeholder = VSplit().set_content_align("c").set_item_align("c").set_sep(4)
    placeholder.add_item(Spacer(w=64, h=64))
    placeholder.add_item(TextBox(" ", TextStyle(font=DEFAULT_BOLD_FONT, size=15, color=gray_color)))
    return placeholder


def _build_area_material(material, icon_cache: dict[str, ImageSource], has_profile: bool) -> VSplit:
    gray_color, red_color, green_color = (50, 50, 50), (200, 0, 0), (0, 200, 0)
    material_widget = VSplit().set_content_align("c").set_item_align("c").set_sep(4)
    icon_frame = Frame()
    size = 64
    material_icon = icon_cache.get(material.material_icon_path)
    if material_icon:
        icon_frame.add_item(ImageBox(material_icon, size=(size, size)))
    icon_frame.add_item(
        TextBox(
            f"x{_get_quant_text(material.quantity)}",
            TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=gray_color),
        )
        .set_offset((size, size))
        .set_offset_anchor("rb")
    )
    material_widget.add_item(icon_frame)
    color = green_color if material.is_enough else red_color
    if not has_profile:
        color = gray_color
    have_text = _get_quant_text(material.have_quantity)
    sum_text = _get_quant_text(material.sum_quantity)
    text = f"{have_text}/{sum_text}" if has_profile else sum_text
    material_widget.add_item(TextBox(text, TextStyle(font=DEFAULT_BOLD_FONT, size=15, color=color)))
    return material_widget


def _build_area_level_row(
    level_info,
    current_level: int,
    can_upgrade: bool,
    icon_cache: dict[str, ImageSource],
    has_profile: bool,
) -> HSplit:
    gray_color = (50, 50, 50)
    row = HSplit().set_content_align("l").set_item_align("l").set_sep(8).set_padding(8)
    level_column = VSplit().set_content_align("c").set_item_align("c").set_sep(4)
    level_column.add_item(
        TextBox(
            str(level_info.level),
            TextStyle(
                font=DEFAULT_BOLD_FONT,
                size=24,
                color=_area_level_color(level_info.level, current_level, can_upgrade, has_profile),
            ),
        )
    )
    level_column.add_item(
        TextBox(
            f"+{level_info.bonus:.1f}%",
            TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=gray_color),
        ).set_w(64)
    )
    row.add_item(level_column)
    if level_info.level <= current_level:
        row.add_item(_build_completed_area_material_placeholder())
    else:
        for material in level_info.materials:
            row.add_item(_build_area_material(material, icon_cache, has_profile))
    return row


def _build_area_item_column(item, icon_cache: dict[str, ImageSource], has_profile: bool) -> VSplit:
    column = (
        VSplit()
        .set_content_align("l")
        .set_item_align("l")
        .set_sep(8)
        .set_item_bg(roundrect_bg(alpha=80))
        .set_padding(8)
    )
    column.add_item(_build_area_item_header(item, icon_cache))
    can_upgrade = True
    for level_info in item.levels:
        if level_info.level > item.current_level:
            can_upgrade = can_upgrade and level_info.can_upgrade
        column.add_item(_build_area_level_row(level_info, item.current_level, can_upgrade, icon_cache, has_profile))
    return column


async def _build_area_item_upgrade_materials_canvas(rqd: AreaItemUpgradeMaterialsRequest) -> Canvas:
    """合成区域道具升级材料图片

    Args:
        rqd: 区域道具升级材料请求数据

    Returns:
        生成的区域道具升级材料图片
    """
    icon_paths = _collect_area_item_icon_paths(rqd.area_items)
    icon_cache = await _load_asset_ref_cache(icon_paths, "compose_area_item_upgrade_materials_image")
    canvas = Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING)
    root = VSplit().set_content_align("lt").set_item_align("lt").set_sep(16)
    if rqd.profile:
        root.add_item(await get_profile_card(rqd.profile.to_profile_card_request()))
    columns = (
        HSplit().set_content_align("lt").set_item_align("lt").set_sep(16).set_bg(roundrect_bg(alpha=80)).set_padding(8)
    )
    for item in rqd.area_items:
        columns.add_item(_build_area_item_column(item, icon_cache, rqd.has_profile))
    root.add_item(columns)
    canvas.add_item(root)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_area_item_upgrade_materials_image(rqd: AreaItemUpgradeMaterialsRequest) -> Image.Image:
    return await (await _build_area_item_upgrade_materials_canvas(rqd)).get_img()


async def try_render_area_item_upgrade_materials_payload(
    rqd: AreaItemUpgradeMaterialsRequest,
) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(
        await _build_area_item_upgrade_materials_canvas(rqd),
        endpoint="education_area_item",
    )


# ========== 羁绊等级 ==========


def _build_education_table_header(labels: list[str], widths: tuple[int, ...], style: TextStyle) -> HSplit:
    header = (
        HSplit()
        .set_content_align("c")
        .set_item_align("c")
        .set_sep(8)
        .set_h(56)
        .set_padding(4)
        .set_bg(roundrect_bg(alpha=80))
    )
    for label, width in zip(labels, widths, strict=True):
        header.add_item(TextBox(label, style).set_w(width).set_content_align("c"))
    return header


def _bond_need_exp_text(bond, max_level: int) -> str:
    if not bond.has_bond:
        return "-"
    if bond.bond_level == max_level:
        return "MAX"
    if bond.need_exp is not None:
        return str(bond.need_exp)
    return "-"


def _bond_level_color(bond, max_level: int) -> tuple[int, int, int, int]:
    if min(bond.chara_rank1, bond.chara_rank2) <= bond.bond_level < max_level:
        return (150, 0, 0, 255)
    return (50, 50, 50, 255)


def _build_bond_row(
    bond,
    icons: tuple[ImageSource | None, ImageSource | None],
    index: int,
    max_level: int,
    text_style: TextStyle,
    widths: tuple[int, int, int, int, int],
) -> HSplit:
    w1, w2, w3, w4, w5 = widths
    row = (
        HSplit()
        .set_content_align("c")
        .set_item_align("c")
        .set_sep(8)
        .set_h(48)
        .set_padding(4)
        .set_bg(roundrect_bg(fill=_info_panel_row_fill(index)))
    )
    icon_frame = Frame().set_w(w1).set_content_align("c")
    for icon, offset in zip(icons, (-13, 13), strict=True):
        if icon:
            icon_frame.add_item(ImageBox(icon, size=(None, 40)).set_offset((offset, 0)))
    row.add_item(icon_frame)

    level_color = _bond_level_color(bond, max_level)
    bold_level_style = text_style.replace(font=DEFAULT_BOLD_FONT, color=level_color)
    row.add_item(TextBox(f"{bond.chara_rank1} & {bond.chara_rank2}", bold_level_style).set_w(w2).set_content_align("c"))
    row.add_item(
        TextBox(str(bond.bond_level) if bond.bond_level else "-", bold_level_style).set_w(w3).set_content_align("c")
    )
    row.add_item(
        _build_progress_bar(
            width=w4,
            current=bond.bond_level,
            maximum=max_level,
            fill=LinearGradient(c1=bond.color1, c2=bond.color2, p1=(0, 0.5), p2=(1, 0.5)),
            tick_step=10,
            enabled=bond.has_bond,
        )
    )
    row.add_item(TextBox(_bond_need_exp_text(bond, max_level), text_style).set_w(w5).set_content_align("c"))
    return row


async def _build_bonds_canvas(rqd: BondsRequest) -> Canvas:
    """合成羁绊等级图片

    Args:
        rqd: 羁绊等级请求数据

    Returns:
        生成的羁绊等级图片
    """
    header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(25, 25, 25, 255))
    text_style = TextStyle(font=DEFAULT_FONT, size=20, color=(50, 50, 50, 255))
    widths = (100, 120, 100, 350, 150)
    icon_paths = [path for bond in rqd.bonds for path in (bond.chara_icon_path1, bond.chara_icon_path2)]
    bond_icons = await _load_asset_refs(icon_paths, "compose_bonds_image")

    canvas = Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING)
    root = VSplit().set_content_align("lt").set_item_align("lt").set_sep(16)
    root.add_item(await get_profile_card(rqd.profile.to_profile_card_request()))
    table = (
        VSplit().set_content_align("l").set_item_align("l").set_sep(8).set_padding(16).set_bg(roundrect_bg(alpha=80))
    )
    labels = ["角色", "角色等级", "羁绊等级", f"进度(上限{rqd.max_level}级)", "升级经验"]
    table.add_item(_build_education_table_header(labels, widths, header_style))
    for index, bond in enumerate(rqd.bonds):
        icon_pair = (bond_icons[index * 2], bond_icons[index * 2 + 1])
        table.add_item(_build_bond_row(bond, icon_pair, index, rqd.max_level, text_style, widths))
    root.add_item(table)
    canvas.add_item(root)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_bonds_image(rqd: BondsRequest) -> Image.Image:
    return await (await _build_bonds_canvas(rqd)).get_img()


async def try_render_bonds_payload(rqd: BondsRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(
        await _build_bonds_canvas(rqd),
        endpoint="education_bonds",
    )


# ========== 队长次数 ==========


def _build_leader_count_row(
    info,
    chara_icon: ImageSource | None,
    index: int,
    max_play_count: int,
    text_style: TextStyle,
    widths: tuple[int, int, int, int, int],
) -> HSplit:
    w1, w2, w3, w4, w5 = widths
    row = (
        HSplit()
        .set_content_align("c")
        .set_item_align("c")
        .set_sep(8)
        .set_h(48)
        .set_padding(4)
        .set_bg(roundrect_bg(fill=_info_panel_row_fill(index)))
    )
    icon_frame = Frame().set_w(w1).set_content_align("c")
    if chara_icon:
        icon_frame.add_item(ImageBox(chara_icon, size=(None, 40)))
    row.add_item(icon_frame)

    bold_style = text_style.replace(font=DEFAULT_BOLD_FONT)
    row.add_item(TextBox(str(info.play_count) if info.play_count else "-", bold_style).set_w(w2).set_content_align("c"))
    row.add_item(TextBox(f"x{info.ex_level}" if info.ex_level else "-", bold_style).set_w(w3).set_content_align("c"))
    row.add_item(TextBox(str(info.ex_count) if info.ex_count else "-", bold_style).set_w(w4).set_content_align("c"))
    row.add_item(
        _build_progress_bar(
            width=w5,
            current=info.play_count,
            maximum=max_play_count,
            fill=_leader_count_color(info.play_count),
            tick_step=10_000,
        )
    )
    return row


async def _build_leader_count_canvas(rqd: LeaderCountRequest) -> Canvas:
    """合成队长次数图片

    Args:
        rqd: 队长次数请求数据

    Returns:
        生成的队长次数图片
    """
    header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(25, 25, 25, 255))
    text_style = TextStyle(font=DEFAULT_FONT, size=20, color=(50, 50, 50, 255))
    widths = (80, 100, 100, 100, 350)
    leader_icons = await _load_asset_refs(
        [info.chara_icon_path for info in rqd.leader_counts],
        "compose_leader_count_image",
    )

    canvas = Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING)
    root = VSplit().set_content_align("lt").set_item_align("lt").set_sep(16)
    root.add_item(await get_profile_card(rqd.profile.to_profile_card_request()))
    table = (
        VSplit().set_content_align("l").set_item_align("l").set_sep(8).set_padding(16).set_bg(roundrect_bg(alpha=80))
    )
    labels = ["角色", "队长次数", "EX等级", "EX次数", f"进度(上限{rqd.max_play_count})"]
    table.add_item(_build_education_table_header(labels, widths, header_style))
    for index, (info, icon) in enumerate(zip(rqd.leader_counts, leader_icons, strict=True)):
        table.add_item(_build_leader_count_row(info, icon, index, rqd.max_play_count, text_style, widths))
    root.add_item(table)
    canvas.add_item(root)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_leader_count_image(rqd: LeaderCountRequest) -> Image.Image:
    return await (await _build_leader_count_canvas(rqd)).get_img()


async def try_render_leader_count_payload(rqd: LeaderCountRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(
        await _build_leader_count_canvas(rqd),
        endpoint="education_leader_count",
    )


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


def _build_character_mission_card_rows(rows, card_w: int) -> list[HSplit]:
    card_rows = []
    for index in range(0, len(rows), 2):
        row = HSplit().set_content_align("lt").set_item_align("lt").set_sep(16)
        row.add_item(_build_character_mission_card(rows[index], card_w))
        second_card = (
            _build_character_mission_card(rows[index + 1], card_w) if index + 1 < len(rows) else Spacer(card_w, 1)
        )
        row.add_item(second_card)
        card_rows.append(row)
    return card_rows


def _build_character_mission_panel(title: str, title_style: TextStyle) -> VSplit:
    panel = (
        VSplit()
        .set_bg(_character_mission_panel_bg())
        .set_padding(16)
        .set_sep(12)
        .set_content_align("lt")
        .set_item_align("lt")
    )
    panel.add_item(TextBox(title, title_style))
    return panel


def _build_character_mission_rows_panel(
    title: str,
    rows,
    card_w: int,
    empty_text: str,
    title_style: TextStyle,
) -> VSplit:
    panel = _build_character_mission_panel(title, title_style)
    if rows:
        for card_row in _build_character_mission_card_rows(rows, card_w):
            panel.add_item(card_row)
    else:
        panel.add_item(TextBox(empty_text, TextStyle(font=DEFAULT_FONT, size=18, color=(80, 80, 80, 255))))
    return panel


_DUAL_ACHIEVEMENT_TYPES = ("play_live", "play_live_ex", "waiting_room", "waiting_room_ex")


def _build_character_mission_achievement_panel(rows, card_w: int, title_style: TextStyle) -> VSplit:
    panel = _build_character_mission_panel("成就", title_style)
    by_type = {row.mission_type: row for row in rows}
    dual_rows = [by_type.get(mission_type) for mission_type in _DUAL_ACHIEVEMENT_TYPES]
    has_dual_rows = all(dual_rows)
    if has_dual_rows:
        play_live, play_live_ex, waiting_room, waiting_room_ex = dual_rows
        dual_row = HSplit().set_content_align("lt").set_item_align("lt").set_sep(16)
        dual_row.add_item(_build_character_mission_dual_card("队长次数", play_live, play_live_ex, card_w))
        dual_row.add_item(_build_character_mission_dual_card("休息室次数", waiting_room, waiting_room_ex, card_w))
        panel.add_item(dual_row)

    remaining_rows = [row for row in rows if row.mission_type not in _DUAL_ACHIEVEMENT_TYPES]
    for card_row in _build_character_mission_card_rows(remaining_rows, card_w):
        panel.add_item(card_row)
    if not remaining_rows and not has_dual_rows:
        panel.add_item(TextBox("暂无可显示的成就任务", TextStyle(font=DEFAULT_FONT, size=18, color=(80, 80, 80, 255))))
    return panel


def _build_character_mission_note_panel(note_style: TextStyle) -> VSplit:
    panel = (
        VSplit()
        .set_content_align("l")
        .set_item_align("l")
        .set_sep(8)
        .set_padding(12)
        .set_bg(_character_mission_panel_bg())
    )
    panel.add_item(
        TextBox(
            "各任务上限为MasterData中所规定的上限，并不一定是当前已实装资源总数",
            note_style,
            use_real_line_count=True,
        )
    )
    return panel


def _build_character_mission_summary_panel(
    rqd: CharacterMissionOverviewRequest,
    chara_icon: ImageSource,
    header_style: TextStyle,
) -> VSplit:
    panel = (
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
    panel.add_item(header_row)
    return panel


async def _build_character_mission_overview_canvas(rqd: CharacterMissionOverviewRequest) -> Canvas:
    chara_icon = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.character_icon_path)
    header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(25, 25, 25, 255))
    sub_header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(35, 35, 35, 255))
    note_style = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(0, 0, 0, 255))
    card_w = 520

    canvas = Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING)
    root = VSplit().set_content_align("lt").set_item_align("lt").set_sep(16)
    root.add_item(await get_profile_card(rqd.profile.to_profile_card_request()))
    root.add_item(_build_character_mission_note_panel(note_style))
    root.add_item(_build_character_mission_summary_panel(rqd, chara_icon, header_style))
    root.add_item(
        _build_character_mission_rows_panel(
            "基本任务",
            rqd.basic_rows,
            card_w,
            "暂无可显示的基本任务",
            sub_header_style,
        )
    )
    root.add_item(_build_character_mission_achievement_panel(rqd.achievement_rows, card_w, sub_header_style))

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
    return await render_canvas_payload(
        await _build_character_mission_overview_canvas(rqd),
        endpoint="education_character_mission_overview",
    )


_MISSION_TABLE_DEFAULT_CHUNK_SIZE = 40
_MISSION_TABLE_COLUMNS = (
    ("档位", 84, "seq", "#"),
    ("需求", 96, "requirement", ""),
    ("累计需求", 128, "acc_requirement", ""),
    ("EXP", 72, "exp", ""),
    ("累计EXP", 116, "acc_exp", ""),
)


def _character_mission_table_chunks(section, target_col_count: int | None) -> list[list]:
    chunk_size = _MISSION_TABLE_DEFAULT_CHUNK_SIZE
    if target_col_count and target_col_count > 1:
        chunk_size = max(1, math.ceil(len(section.display_rows) / target_col_count))
    return [
        section.display_rows[index : index + chunk_size] for index in range(0, len(section.display_rows), chunk_size)
    ] or [[]]


def _character_mission_table_cell_bg(row, index: int, reached_seq: int) -> RoundRectBg:
    fill = (255, 244, 196, 210) if row.seq == reached_seq and reached_seq > 0 else _info_panel_row_fill(index)
    return roundrect_bg(fill=fill)


def _build_character_mission_table_column(
    title: str,
    width: int,
    attribute: str,
    prefix: str,
    rows,
    reached_seq: int,
    header_style: TextStyle,
    cell_style: TextStyle,
) -> VSplit:
    column = VSplit().set_content_align("c").set_item_align("c").set_sep(6)
    column.add_item(TextBox(title, header_style).set_size((width, 40)).set_content_align("c"))
    for index, row in enumerate(rows):
        value = f"{prefix}{getattr(row, attribute)}"
        column.add_item(
            TextBox(value, cell_style)
            .set_bg(_character_mission_table_cell_bg(row, index, reached_seq))
            .set_size((width, 40))
            .set_content_align("c")
        )
    return column


def _build_character_mission_table_chunk(
    rows,
    reached_seq: int,
    header_style: TextStyle,
    cell_style: TextStyle,
) -> HSplit:
    chunk = HSplit().set_content_align("lt").set_item_align("lt").set_sep(6)
    for title, width, attribute, prefix in _MISSION_TABLE_COLUMNS:
        chunk.add_item(
            _build_character_mission_table_column(
                title,
                width,
                attribute,
                prefix,
                rows,
                reached_seq,
                header_style,
                cell_style,
            )
        )
    return chunk


def _build_character_mission_section_header(
    section,
    header_style: TextStyle,
    cell_style: TextStyle,
    progress_style: TextStyle,
) -> HSplit:
    header = HSplit().set_content_align("lb").set_item_align("lb").set_sep(8)
    header.add_item(TextBox("当前进度:", header_style))
    header.add_item(TextBox(str(section.current_total), progress_style))
    if section.is_ex and section.current_round_no is not None:
        header.add_item(TextBox(f"当前回目 EX {section.current_round_no}", cell_style))
    elif section.reached_seq > 0:
        header.add_item(TextBox(f"已达档位 #{section.reached_seq}", cell_style))
    return header


def _build_character_mission_section_table(section, target_col_count: int | None = None) -> VSplit:
    title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=(35, 35, 35, 255))
    header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=BLACK)
    cell_style = TextStyle(font=DEFAULT_FONT, size=20, color=(50, 50, 50))
    progress_style = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(200, 50, 50))
    root = (
        VSplit()
        .set_content_align("lt")
        .set_item_align("lt")
        .set_sep(8)
        .set_padding(8)
        .set_bg(_character_mission_panel_bg())
    )
    root.add_item(TextBox("EX任务" if section.is_ex else "普通任务", title_style))
    root.add_item(_build_character_mission_section_header(section, header_style, cell_style, progress_style))
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
    column_wrap = HSplit().set_content_align("lt").set_item_align("lt").set_sep(12)
    for rows in _character_mission_table_chunks(section, target_col_count):
        column_wrap.add_item(_build_character_mission_table_chunk(rows, section.reached_seq, header_style, cell_style))
    root.add_item(column_wrap)
    return root


def _character_mission_normal_column_count(sections) -> int | None:
    normal_section = next((section for section in sections if not section.is_ex), None)
    if normal_section is None:
        return None
    return max(1, math.ceil(len(normal_section.display_rows) / _MISSION_TABLE_DEFAULT_CHUNK_SIZE))


def _build_character_mission_empty_table(cell_style: TextStyle) -> VSplit:
    panel = (
        VSplit()
        .set_content_align("lt")
        .set_item_align("lt")
        .set_sep(8)
        .set_padding(8)
        .set_bg(_character_mission_panel_bg())
    )
    panel.add_item(TextBox("没有可显示的任务表数据", cell_style))
    return panel


async def _build_character_mission_all_canvas(rqd: CharacterMissionAllRequest) -> Canvas:
    chara_icon = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.character_icon_path)
    title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=26, color=BLACK)
    style2 = TextStyle(font=DEFAULT_FONT, size=20, color=(50, 50, 50))

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

    normal_col_count = _character_mission_normal_column_count(rqd.sections)
    if rqd.sections:
        for section in rqd.sections:
            target_col_count = normal_col_count if section.is_ex and normal_col_count else None
            root.add_item(_build_character_mission_section_table(section, target_col_count))
    else:
        root.add_item(_build_character_mission_empty_table(style2))

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
    return await render_canvas_payload(
        await _build_character_mission_all_canvas(rqd),
        endpoint="education_character_mission_all",
    )
