import asyncio
import logging
import time

from PIL import Image

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.draw import (
    BG_PADDING,
    DIFF_COLORS,
    SEKAI_BLUE_BG,
    Canvas,
    TextBox,
    add_request_watermark,
    roundrect_bg,
)
from src.sekai.base.painter import BLACK, WHITE, get_font, get_text_size
from src.sekai.base.plot import (
    FillBg,
    HSplit,
    ImageBox,
    RoundRectBg,
    Spacer,
    TextStyle,
    VSplit,
)
from src.sekai.base.utils import ImageSource, get_asset_image_ref
from src.sekai.skia_renderer.canvas import render_canvas_payload, skia_plot_enabled
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT

# =========================== 从.model导入数据类型 =========================== #
from .model import (
    CustomRoomScoreRequest,
    MusicBoardRequest,
    MusicMetaRequest,
    ScoreControlRequest,
)

logger = logging.getLogger(__name__)
_POINTS_PER_TIME_TARGET = "pt/time"


def _calc_custom_room_title_width(
    column_width: int,
    music_count: int,
    row_padding: int,
    item_sep: int,
    cover_size: int,
    title_style: TextStyle,
) -> int:
    """Reserve enough room for cover icons and separators, then clip each title into the remaining width."""
    if music_count <= 0:
        return max(24, column_width - row_padding * 2)

    separator_count = max(music_count - 1, 0)
    item_count = music_count * 2 + separator_count
    gap_count = max(item_count - 1, 0)
    separator_font = get_font(title_style.font, title_style.size)
    separator_width, _ = get_text_size(separator_font, " / ")
    usable_width = column_width - row_padding * 2
    usable_width -= cover_size * music_count
    usable_width -= separator_width * separator_count
    usable_width -= item_sep * gap_count
    return max(24, usable_width // music_count)


async def _load_custom_room_covers(music_list_map: dict) -> dict[str, ImageSource]:
    cover_paths = {music["music_cover"] for music_list in music_list_map.values() for music in music_list}
    if not cover_paths:
        return {}
    cover_list = list(cover_paths)
    started_at = time.perf_counter()
    cover_images = await asyncio.gather(*[get_asset_image_ref(ASSETS_BASE_DIR, path) for path in cover_list])
    logger.debug(
        "[perf] compose_custom_room_score_control_image preload %d covers: %.3fs",
        len(cover_list),
        time.perf_counter() - started_at,
    )
    return dict(zip(cover_list, cover_images))


def _custom_room_music_list(music_list_map: dict, event_rate: int) -> list[dict]:
    return music_list_map.get(str(event_rate), []) or music_list_map.get(int(event_rate), [])


def _draw_custom_room_music_row(
    music_list_map: dict,
    cover_cache: dict[str, ImageSource],
    event_rate: int,
    column_width: int,
    row_padding: int,
    item_sep: int,
    cover_size: int,
    style: TextStyle,
) -> None:
    music_list = _custom_room_music_list(music_list_map, event_rate)
    if not music_list:
        TextBox("-", style)
        return

    title_width = _calc_custom_room_title_width(
        column_width,
        len(music_list),
        row_padding,
        item_sep,
        cover_size,
        style,
    )
    for index, music_info in enumerate(music_list):
        if index > 0:
            # Keep separator width consistent with _calc_custom_room_title_width
            # (TextBox has default horizontal padding=2, which may cause overflow).
            TextBox(" / ", style).set_padding(0)
        music_cover = cover_cache[music_info["music_cover"]]
        ImageBox(music_cover, size=(cover_size, cover_size), use_alpha_blend=False)
        TextBox(str(music_info["music_title"]), style, line_count=1).set_w(title_width)


# 合成控分图片
async def _build_score_control_canvas(
    rqd: ScoreControlRequest,
) -> Canvas:
    r"""compose_score_control_image

    合成控分图片 (普通房间)

    Args
    ----
    rqd : ScoreControlRequest
        绘制控分图片所必须的数据

    Returns
    -------
    PIL.Image.Image
    """
    SHOW_SEG_LEN = 50

    def get_score_str(score: int) -> str:
        score_str = str(score)
        score_str = score_str[::-1]
        score_str = ",".join([score_str[i : i + 4] for i in range(0, len(score_str), 4)])
        return score_str[::-1]

    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=BLACK)
    style2 = TextStyle(font=DEFAULT_FONT, size=16, color=(50, 50, 50))
    style3 = TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(255, 50, 50))

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_item_bg(roundrect_bg(alpha=80)):
            # 标题
            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_padding(8):
                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(4):
                    music_cover = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.music_cover_path)
                    ImageBox(music_cover, size=(20, 20), use_alpha_blend=False)
                    TextBox(f"【{rqd.music_id}】{rqd.music_title} (任意难度)", style1)
                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(4):
                    TextBox(f"歌曲基础分 {rqd.music_basic_point}   目标PT: ", style1)
                    TextBox(f" {rqd.target_point}", style3)
                if rqd.music_basic_point != 100 and rqd.target_point > 1000:
                    TextBox("基础分非100有误差风险，不推荐控较大PT", style3)
                if rqd.target_point > 3000:
                    TextBox("目标PT过大可能存在误差，推荐以多次控分", style3)
                TextBox("控分教程：选取表中一个活动加成和体力", style1)
                TextBox("游玩歌曲到对应分数范围内放置", style1)
                TextBox("友情提醒：控分前请核对加成和体力设置", style3)
                TextBox("特别注意核对加成是否多了0.5", style3)

            # 数据
            with (
                HSplit()
                .set_content_align("lt")
                .set_item_align("lt")
                .set_sep(8)
                .set_omit_parent_bg(True)
                .set_item_bg(roundrect_bg(alpha=80))
            ):
                for i in range(0, len(rqd.valid_scores), SHOW_SEG_LEN):
                    scores = rqd.valid_scores[i : i + SHOW_SEG_LEN]
                    gh, gw1, gw2, gw3, gw4 = 20, 54, 48, 90, 90
                    bg1 = FillBg((255, 255, 255, 200))
                    bg2 = FillBg((255, 255, 255, 100))
                    with VSplit().set_content_align("lt").set_item_align("lt").set_sep(4).set_padding(8):
                        with HSplit().set_content_align("lt").set_item_align("lt").set_sep(4):
                            TextBox("加成", style1).set_bg(bg1).set_size((gw1, gh)).set_content_align("c")
                            TextBox("火", style1).set_bg(bg1).set_size((gw2, gh)).set_content_align("c")
                            TextBox("分数下限", style1).set_bg(bg1).set_size((gw3, gh)).set_content_align("c")
                            TextBox("分数上限", style1).set_bg(bg1).set_size((gw4, gh)).set_content_align("c")
                        for row_index, item in enumerate(scores):
                            bg = bg2 if row_index % 2 == 0 else bg1
                            score_min = get_score_str(item.score_min)
                            if score_min == "0":
                                score_min = "0 (放置)"
                            score_max = get_score_str(item.score_max)
                            with HSplit().set_content_align("lt").set_item_align("lt").set_sep(4):
                                TextBox(f"{item.event_bonus}", style2).set_bg(bg).set_size((gw1, gh)).set_content_align(
                                    "r"
                                )
                                TextBox(f"{item.boost}", style2).set_bg(bg).set_size((gw2, gh)).set_content_align("r")
                                TextBox(f"{score_min}", style2).set_bg(bg).set_size((gw3, gh)).set_content_align("r")
                                TextBox(f"{score_max}", style2).set_bg(bg).set_size((gw4, gh)).set_content_align("r")

    add_request_watermark(canvas, rqd)
    return canvas


# 合成自定义房间控分图片
async def _build_custom_room_score_control_canvas(rqd: CustomRoomScoreRequest) -> Canvas:
    r"""compose_custom_room_score_control_image

    合成自定义房间控分图片

    Args
    ----
    rqd : CustomRoomScoreRequest
        绘制信息

    Returns
    -------
    PIL.Image.Image
    """
    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=BLACK)
    style2 = TextStyle(font=DEFAULT_FONT, size=20, color=(50, 50, 50))
    style3 = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(200, 50, 50))

    # 预加载所有歌曲封面（并行）
    cover_cache = await _load_custom_room_covers(rqd.music_list_map)

    # 合成图片
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with (
            VSplit()
            .set_content_align("lt")
            .set_item_align("lt")
            .set_sep(8)
            .set_padding(16)
            .set_bg(roundrect_bg(alpha=80))
        ):
            # 标题
            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8):
                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(4):
                    TextBox("自定义房间控分 目标PT: ", style1)
                    TextBox(f" {rqd.target_point}", style3)
                TextBox(
                    """
该方法用于距离目标PT不足100时补救，使用方式:
1. 选定表格中的一组歌曲和活动加成
2. 自己配置好活动加成（注意检查小数），并将体力设置为0
3. 创建自定义房间，邀请另一个玩家进入房间
4. 选择该歌曲（任意难度），两个人均放置整首歌
""".strip(),
                    style2,
                    use_real_line_count=True,
                )
                TextBox(
                    """
若有上传Suite抓包，使用"/控分组卡"可以更快配出队伍
可用同PT系数的歌曲替代表中歌曲
数据来自x@SYLVIA0x0，目前验证不足仅供参考
""".strip(),
                    style2,
                    use_real_line_count=True,
                )

            # 数据
            gh, vsep, hsep = 40, 6, 6
            w1, w2, w3 = 140, 520, 100
            music_row_padding = 8
            music_item_sep = 4
            cover_size = gh - 2

            def bg_fn(i: int):
                return FillBg((255, 255, 255, 200)) if i % 2 == 0 else FillBg((255, 255, 255, 100))

            with HSplit().set_content_align("lt").set_item_align("lt").set_sep(hsep):
                # 活动加成
                with VSplit().set_content_align("c").set_item_align("c").set_sep(vsep):
                    TextBox("活动加成", style1).set_size((w1, gh)).set_content_align("c").set_bg(bg_fn(0))
                    for i, (_, event_bonus) in enumerate(rqd.candidate_pairs):
                        bg = bg_fn(i + 1)
                        TextBox(f"{event_bonus} %", style2).set_size((w1, gh)).set_content_align("c").set_padding(
                            (16, 0)
                        ).set_bg(bg)
                # 歌曲
                with VSplit().set_content_align("c").set_item_align("c").set_sep(vsep):
                    TextBox("可用歌曲", style1).set_size((w2, gh)).set_content_align("c").set_bg(bg_fn(0))
                    for i, (event_rate, _) in enumerate(rqd.candidate_pairs):
                        bg = bg_fn(i + 1)
                        with (
                            HSplit()
                            .set_content_align("c")
                            .set_item_align("c")
                            .set_sep(music_item_sep)
                            .set_padding((music_row_padding, 0))
                            .set_size((w2, gh))
                            .set_bg(bg)
                        ):
                            _draw_custom_room_music_row(
                                rqd.music_list_map,
                                cover_cache,
                                event_rate,
                                w2,
                                music_row_padding,
                                music_item_sep,
                                cover_size,
                                style2,
                            )
                # PT系数
                with VSplit().set_content_align("c").set_item_align("c").set_sep(vsep):
                    TextBox("PT系数", style1).set_size((w3, gh)).set_content_align("c").set_bg(bg_fn(0))
                    for i, (event_rate, _) in enumerate(rqd.candidate_pairs):
                        bg = bg_fn(i + 1)
                        TextBox(f"{event_rate}", style2).set_size((w3, gh)).set_content_align("c").set_padding(
                            (8, 0)
                        ).set_bg(bg)

    add_request_watermark(canvas, rqd)
    return canvas


# 合成歌曲meta图片
async def _build_music_meta_canvas(requests: list[MusicMetaRequest]) -> Canvas:
    r"""compose_music_meta_image

    合成歌曲Meta图片，支持多首歌曲对比

    Args
    ----
    requests : List[MusicMetaRequest]
        歌曲Meta信息请求列表
    """
    # 预加载所有歌曲封面
    _t0 = time.perf_counter()
    _meta_covers = await asyncio.gather(
        *[get_asset_image_ref(ASSETS_BASE_DIR, rqd.music_cover_path) for rqd in requests]
    )
    logger.debug(
        "[perf] compose_music_meta_image preload %d covers: %.3fs",
        len(requests),
        time.perf_counter() - _t0,
    )

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with HSplit().set_content_align("lt").set_item_align("lt").set_sep(8):
            for rqd, music_cover in zip(requests, _meta_covers):
                style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=BLACK)
                style2 = TextStyle(font=DEFAULT_FONT, size=20, color=(50, 50, 50))

                with (
                    VSplit()
                    .set_content_align("lt")
                    .set_item_align("lt")
                    .set_sep(8)
                    .set_bg(roundrect_bg(alpha=80))
                    .set_padding(16)
                ):
                    # 歌曲标题
                    with HSplit().set_content_align("l").set_item_align("l").set_sep(4):
                        ImageBox(music_cover, size=(48, 48), use_alpha_blend=False)
                        TextBox(
                            f"【{rqd.music_id}】{rqd.music_title}",
                            TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK),
                        )
                    TextBox(
                        "以日服为准，参考分数使用5张技能加分100%，数据来源：33Kit",
                        TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=BLACK),
                    )

                    # 信息
                    with (
                        VSplit()
                        .set_content_align("lt")
                        .set_item_align("lt")
                        .set_sep(8)
                        .set_item_bg(roundrect_bg(alpha=80))
                    ):
                        for meta in rqd.metas:
                            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_padding(8):
                                diff = meta.difficulty

                                # Best skill order solo calculation (simplified logic for display)
                                # Assuming caller handled best skill order or just display index 1-5 ordered by value
                                # Here we just re-implement the sorting logic for visual
                                best_skill_order_solo = list(range(5))
                                # We need to access skill_score_solo, but it's in the model
                                scores_solo = meta.skill_score_solo
                                best_skill_order_solo.sort(key=lambda x: scores_solo[x], reverse=True)
                                best_skill_order_solo_idx = [best_skill_order_solo.index(i) for i in range(5)]

                                solo_skill, auto_skill, multi_skill = 1.0, 1.0, 1.8

                                solo_score = meta.base_score + sum(meta.skill_score_solo) * solo_skill
                                auto_score = meta.base_score_auto + sum(meta.skill_score_auto) * auto_skill
                                multi_score = (
                                    meta.base_score
                                    + sum(meta.skill_score_multi) * multi_skill
                                    + meta.fever_score * 0.5
                                    + 0.01875
                                )

                                solo_skill_account = sum(meta.skill_score_solo) * solo_skill / solo_score
                                auto_skill_account = sum(meta.skill_score_auto) * auto_skill / auto_score
                                multi_skill_account = sum(meta.skill_score_multi) * multi_skill / multi_score

                                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(0):
                                    TextBox(
                                        diff.upper(), TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=WHITE)
                                    ).set_bg(RoundRectBg(DIFF_COLORS.get(diff, (0, 0, 0)), radius=6)).set_padding(4)
                                    Spacer(w=8)
                                    TextBox("时长", style1)
                                    TextBox(f" {meta.music_time}s", style2)
                                    TextBox("  每秒点击数", style1)
                                    TextBox(f" {meta.tap_count / meta.music_time:.1f}", style2)
                                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(0):
                                    TextBox("基础分数", style1)
                                    TextBox("（单人）", style1)
                                    TextBox(f" {meta.base_score * 100:.1f}%", style2)
                                    TextBox("  （AUTO）", style1)
                                    TextBox(f" {meta.base_score_auto * 100:.1f}%", style2)
                                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(0):
                                    TextBox("Fever分数", style1)
                                    TextBox(f" {meta.fever_score * 100:.1f}%", style2)
                                    TextBox("  活动PT系数", style1)
                                    TextBox(f" {meta.event_rate:.0f}", style2)
                                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(0):
                                    TextBox("技能分数（单人）", style1)
                                    for s in meta.skill_score_solo:
                                        TextBox(f"  {s * 100:.1f}%", style2)
                                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(0):
                                    TextBox("技能分数（多人）", style1)
                                    for s in meta.skill_score_multi:
                                        TextBox(f"  {s * 100:.1f}%", style2)
                                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(0):
                                    TextBox("技能分数（AUTO）", style1)
                                    for s in meta.skill_score_auto:
                                        TextBox(f"  {s * 100:.1f}%", style2)
                                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(0):
                                    TextBox("单人最优技能顺序（1-5代表强到弱的卡牌）", style1)
                                    for idx in best_skill_order_solo_idx:
                                        TextBox(f" {idx + 1}", style2)
                                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(0):
                                    TextBox("参考分数", style1)
                                    TextBox("（单人）", style1)
                                    TextBox(f" {solo_score * 100:.1f}%", style2)
                                    TextBox("（AUTO）", style1)
                                    TextBox(f" {auto_score * 100:.1f}%", style2)
                                    TextBox("（多人）", style1)
                                    TextBox(f" {multi_score * 100:.1f}%", style2)
                                with HSplit().set_content_align("lb").set_item_align("lb").set_sep(0):
                                    TextBox("技能占比", style1)
                                    TextBox("（单人）", style1)
                                    TextBox(f" {solo_skill_account * 100:.1f}%", style2)
                                    TextBox("（AUTO）", style1)
                                    TextBox(f" {auto_skill_account * 100:.1f}%", style2)
                                    TextBox("（多人）", style1)
                                    TextBox(f" {multi_skill_account * 100:.1f}%", style2)

    add_request_watermark(canvas, requests)
    return canvas


def _music_board_columns(target: str) -> list[tuple[str, float, str]]:
    columns = [("排名", 1.2, "c"), ("歌曲", 6.0, "l"), ("难度", 1.5, "c")]
    if target == "score":
        columns.append(("分数", 2.0, "c"))
    elif target in ("pt", _POINTS_PER_TIME_TARGET):
        columns.extend((("PT", 2.0, "c"), ("LIVE分数", 2.0, "c")))
    if target == _POINTS_PER_TIME_TARGET:
        columns.append(("PT/h", 2.0, "c"))
    columns.append(("技能占比", 2.0, "c"))
    if target in (_POINTS_PER_TIME_TARGET, "time"):
        columns.append(("周回/h", 2.0, "c"))
    if target in ("pt", _POINTS_PER_TIME_TARGET, "time"):
        columns.append(("PT系数", 1.5, "c"))
    columns.extend((("时长", 1.5, "c"), ("每秒点击", 1.5, "c")))
    return columns


def _music_board_row_bg(index: int) -> FillBg:
    return FillBg((255, 255, 255, 160)) if index % 2 == 0 else FillBg((255, 255, 255, 60))


def _draw_music_board_rank_column(
    rqd: MusicBoardRequest,
    width: int,
    row_height: int,
    row_sep: int,
    title_style: TextStyle,
    item_style: TextStyle,
) -> None:
    with VSplit().set_content_align("c").set_item_align("c").set_sep(row_sep):
        TextBox("排名", title_style).set_size((width, row_height)).set_content_align("c").set_bg(_music_board_row_bg(0))
        for index, row in enumerate(rqd.items):
            style = item_style
            if (row.music_id, row.difficulty) in rqd.spec_mid_diffs:
                style = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(255, 50, 50))
            TextBox(f"#{row.rank}", style).set_size((width, row_height)).set_content_align("c").set_bg(
                _music_board_row_bg(index + 1)
            )


async def _draw_music_board_song_column(
    rqd: MusicBoardRequest,
    width: int,
    row_height: int,
    row_sep: int,
    title_style: TextStyle,
    item_style: TextStyle,
) -> None:
    with VSplit().set_content_align("c").set_item_align("c").set_sep(row_sep):
        TextBox("歌曲", title_style).set_size((width, row_height)).set_content_align("c").set_bg(_music_board_row_bg(0))
        song_row_padding = 8
        song_row_sep = 4
        song_cover_size = row_height - 8
        song_title_width = max(24, width - song_row_padding * 2 - song_cover_size - song_row_sep)
        for index, row in enumerate(rqd.items):
            with (
                HSplit()
                .set_content_align("l")
                .set_item_align("l")
                .set_sep(song_row_sep)
                .set_padding((song_row_padding, 0))
                .set_size((width, row_height))
                .set_bg(_music_board_row_bg(index + 1))
            ):
                music_cover = await get_asset_image_ref(ASSETS_BASE_DIR, row.music_cover_path)
                ImageBox(music_cover, size=(song_cover_size, song_cover_size), use_alpha_blend=False)
                TextBox(row.music_title, item_style, wrap=False, overflow="shrink").set_w(song_title_width)


def _draw_music_board_difficulty_column(
    rqd: MusicBoardRequest,
    width: int,
    row_height: int,
    row_sep: int,
    title_style: TextStyle,
) -> None:
    with VSplit().set_content_align("c").set_item_align("c").set_sep(row_sep):
        TextBox("难度", title_style).set_size((width, row_height)).set_content_align("c").set_bg(_music_board_row_bg(0))
        for row in rqd.items:
            difficulty_bg = FillBg(DIFF_COLORS.get(row.difficulty, (200, 200, 200)))
            TextBox(f"{row.level}", TextStyle(DEFAULT_BOLD_FONT, 20, WHITE)).set_size(
                (width, row_height)
            ).set_content_align("c").set_bg(difficulty_bg)


def _draw_music_board_text_column(
    rqd: MusicBoardRequest,
    header_text: str,
    value_getter,
    width: int,
    row_height: int,
    row_sep: int,
    title_style: TextStyle,
    item_style: TextStyle,
) -> None:
    with VSplit().set_content_align("c").set_item_align("c").set_sep(row_sep):
        TextBox(header_text, title_style).set_size((width, row_height)).set_content_align("c").set_bg(
            _music_board_row_bg(0)
        )
        for index, row in enumerate(rqd.items):
            TextBox(value_getter(row), item_style).set_size((width, row_height)).set_content_align("c").set_bg(
                _music_board_row_bg(index + 1)
            )


def _draw_music_board_dynamic_columns(
    rqd: MusicBoardRequest,
    ratios: list[float],
    unit_width: int,
    row_height: int,
    row_sep: int,
    title_style: TextStyle,
    item_style: TextStyle,
) -> None:
    column_index = 3

    def add_column(header_text, value_getter) -> None:
        nonlocal column_index
        width = int(ratios[column_index] * unit_width)
        column_index += 1
        _draw_music_board_text_column(
            rqd, header_text, value_getter, width, row_height, row_sep, title_style, item_style
        )

    if rqd.target == "score":
        add_column("分数", lambda row: f"{(row.live_type_score or 0) * 100:.1f}%")
    elif rqd.target in ("pt", _POINTS_PER_TIME_TARGET):
        add_column("PT", lambda row: f"{row.live_type_pt or 0}")
        add_column("LIVE分数", lambda row: f"{(row.live_type_real_score or 0):.0f}")
    if rqd.target == _POINTS_PER_TIME_TARGET:
        add_column("PT/h", lambda row: f"{(row.live_type_pt_per_hour or 0):.0f}")
    add_column("技能占比", lambda row: f"{(row.live_type_skill_account or 0) * 100:.1f}%")
    if rqd.target in (_POINTS_PER_TIME_TARGET, "time"):
        add_column("周回/h", lambda row: f"{(row.play_count_per_hour or 0):.1f}")
    if rqd.target in ("pt", _POINTS_PER_TIME_TARGET, "time"):
        add_column("PT系数", lambda row: f"{row.event_rate:.0f}")
    add_column("时长", lambda row: f"{row.music_time:.1f}")
    add_column("每秒点击", lambda row: f"{row.tps:.1f}")


# 合成歌曲排行图片
async def _build_music_board_canvas(rqd: MusicBoardRequest) -> Canvas:
    r"""compose_music_board_image

    合成歌曲排行图片

    Args
    ----
    rqd : MusicBoardRequest
        绘制请求数据
    """
    title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK)
    item_style = TextStyle(font=DEFAULT_FONT, size=20, color=BLACK)

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with (
            VSplit()
            .set_content_align("lt")
            .set_item_align("lt")
            .set_sep(8)
            .set_padding(16)
            .set_bg(roundrect_bg(alpha=80))
        ):
            # 标题
            TextBox(rqd.title_text, title_style, use_real_line_count=True)
            if rqd.description:
                TextBox(rqd.description, title_style, use_real_line_count=True)

            columns = _music_board_columns(rqd.target)
            unit_w = 60
            ratios = [c[1] for c in columns]

            # 这里的hsep由HSplit自动处理，不再手动计算
            gh = 40  # 行高
            hsep = 5
            vsep = 5

            # 主容器：水平排列各列
            with HSplit().set_content_align("c").set_item_align("c").set_sep(hsep):
                _draw_music_board_rank_column(rqd, int(ratios[0] * unit_w), gh, vsep, title_style, item_style)
                await _draw_music_board_song_column(rqd, int(ratios[1] * unit_w), gh, vsep, title_style, item_style)
                _draw_music_board_difficulty_column(rqd, int(ratios[2] * unit_w), gh, vsep, title_style)
                _draw_music_board_dynamic_columns(rqd, ratios, unit_w, gh, vsep, title_style, item_style)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_score_control_image(rqd: ScoreControlRequest) -> Image.Image:
    return await (await _build_score_control_canvas(rqd)).get_img()


async def try_render_score_control_payload(rqd: ScoreControlRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_score_control_canvas(rqd), endpoint="score_control")


async def compose_custom_room_score_control_image(rqd: CustomRoomScoreRequest) -> Image.Image:
    return await (await _build_custom_room_score_control_canvas(rqd)).get_img()


async def try_render_custom_room_score_control_payload(
    rqd: CustomRoomScoreRequest,
) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_custom_room_score_control_canvas(rqd), endpoint="score_custom_room")


async def compose_music_meta_image(requests: list[MusicMetaRequest]) -> Image.Image:
    return await (await _build_music_meta_canvas(requests)).get_img()


async def try_render_music_meta_payload(requests: list[MusicMetaRequest]) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_music_meta_canvas(requests), endpoint="score_music_meta")


async def compose_music_board_image(rqd: MusicBoardRequest) -> Image.Image:
    return await (await _build_music_board_canvas(rqd)).get_img()


async def try_render_music_board_payload(rqd: MusicBoardRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_music_board_canvas(rqd), endpoint="score_music_board")
