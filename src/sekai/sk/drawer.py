import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import pairwise
import math
import os

import matplotlib
from matplotlib import font_manager
import matplotlib.dates as mdates
from matplotlib.figure import Figure
import matplotlib.patheffects as patheffects
from matplotlib.ticker import FuncFormatter
from PIL import Image

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.draw import (
    BG_PADDING,
    SEKAI_BLUE_BG,
    add_request_watermark,
    roundrect_bg,
)
from src.sekai.base.painter import BLACK, DEFAULT_BOLD_FONT, DEFAULT_FONT, lerp_color, rgb_to_color_code
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
    VSplit,
)
from src.sekai.base.timezone import datetime_from_millis, request_now
from src.sekai.base.utils import (
    get_asset_image_ref,
    get_readable_datetime,
    get_readable_timedelta,
    plt_fig_to_image,
    truncate,
)
from src.sekai.skia_renderer.canvas import render_canvas_payload, skia_plot_enabled
from src.settings import ASSETS_BASE_DIR, DEFAULT_THREAD_POOL_SIZE

from .model import (
    CFRequest,
    CSBRequest,
    PlayerTraceRequest,
    RankInfo,
    RankTraceRequest,
    SklRequest,
    SKRequest,
    SpeedInfo,
    SpeedRequest,
    TeamInfo,  # noqa: F401 - used in type annotations via Request classes
    WinRateRequest,
)

matplotlib.use("Agg")
_matplotlib_workers = max(1, min(DEFAULT_THREAD_POOL_SIZE, os.cpu_count() or 1))
_matplotlib_executor = ThreadPoolExecutor(max_workers=_matplotlib_workers, thread_name_prefix="sk-matplotlib")
_EVENT_ENDED_TEXT = "活动已结束"
_DEFAULT_PREDICTION_NOTICE = "预测数据仅供参考，请以实际为准规划好冲榜计划"

StyledText = tuple[str, TextStyle]


async def run_matplotlib_plot(func: Callable[[], Image.Image]) -> Image.Image:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_matplotlib_executor, func)


def shutdown_sk_drawer() -> None:
    """关闭 sk drawer 模块持有的线程池"""
    _matplotlib_executor.shutdown(wait=False)


# matplotlib字体
font_paths = []
font_paths.append(ASSETS_BASE_DIR / (DEFAULT_FONT + ".otf"))
font_paths.append(ASSETS_BASE_DIR / (DEFAULT_FONT + ".ttf"))
for path in font_paths:
    try:
        font_manager.fontManager.addfont(path)
        prop = font_manager.FontProperties(fname=path)
        font_name = prop.get_name()
        matplotlib.rcParams["font.family"] = [font_name]
        matplotlib.rcParams["axes.unicode_minus"] = False
    except Exception:
        continue

SK_RECORD_TOLERANCE = timedelta(seconds=70)
SK_CSB_STOP_THRESHOLD = timedelta(minutes=5)
SK_PLAYCOUNT_MYSEKAI_THRESHOLD = 37
RANK_TRACE_SCORE_COLORS = [
    "#1d4ed8",
    "#dc2626",
    "#7c3aed",
    "#d97706",
    "#0891b2",
    "#c026d3",
    "#15803d",
    "#be123c",
    "#4b5563",
    "#a16207",
]
PLOT_LABEL_PATH_EFFECTS = [patheffects.withStroke(linewidth=2.5, foreground="white", alpha=0.9)]


def _collect_skl_display_ranks(current_ranks: list[RankInfo], forecast_columns: list) -> list[int]:
    rank_set = {rank.rank for rank in current_ranks}
    for column in forecast_columns:
        rank_set.update(rank.rank for rank in column.ranks)
    return sorted(rank_set)


def _collect_speed_display_rows(ranks: list[SpeedInfo]) -> list[tuple[int, int, int | None, datetime]]:
    return sorted(
        ((rank.rank, rank.score, rank.speed, rank.record_time) for rank in ranks),
        key=lambda row: row[0],
    )


def _time_to_event_end_text(event_end: datetime, now: datetime) -> str:
    time_to_end = event_end - now
    if time_to_end.total_seconds() <= 0:
        return _EVENT_ENDED_TEXT
    return f"距离活动结束还有{get_readable_timedelta(time_to_end)}"


def _readable_datetime_or_dash(value: datetime | None) -> str:
    if value is None:
        return "-"
    return get_readable_datetime(value, show_original_time=False, use_en_unit=False)


def _rank_score_or_dash(rank: RankInfo | None) -> str:
    if rank is None or rank.score is None:
        return "-"
    return get_board_score_str(rank.score)


def _draw_skl_header(
    rqd: SklRequest,
    event_start: datetime,
    event_end: datetime,
    now: datetime,
    banner_img,
    wl_chara_img,
) -> None:
    with HSplit().set_content_align("rt").set_item_align("rt").set_padding(8).set_sep(7):
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(5):
            TextBox(
                get_event_id_and_name_text(rqd.region, rqd.id, truncate(rqd.name, 16)),
                TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK),
            )
            TextBox(
                f"{event_start.strftime('%Y-%m-%d %H:%M')} ~ {event_end.strftime('%Y-%m-%d %H:%M')}",
                TextStyle(font=DEFAULT_FONT, size=18, color=BLACK),
            )
            TextBox(
                _time_to_event_end_text(event_end, now),
                TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK),
            )
        with Frame().set_content_align("r"):
            if banner_img:
                ImageBox(banner_img, size=(140, None))
            if rqd.wl_cid:
                ImageBox(wl_chara_img, size=(None, 50))


def _draw_skl_grid_cell(text: str, style: TextStyle, bg: FillBg, size: tuple[int, int], align: str) -> None:
    cell = TextBox(text, style, overflow="clip").set_bg(bg).set_size(size).set_content_align(align)
    if align == "r":
        cell.set_padding((16, 0))


def _draw_skl_prediction_rows(
    ranks: list[int],
    current_by_rank: dict[int, RankInfo],
    forecast_by_rank: list[dict[int, RankInfo]],
    item_style: TextStyle,
    bg1: FillBg,
    bg2: FillBg,
    gw: int,
    gh: int,
) -> None:
    for index, rank in enumerate(ranks):
        bg = bg2 if index % 2 == 0 else bg1
        _draw_skl_grid_cell(get_board_rank_str(rank), item_style, bg, (gw, gh), "c")
        _draw_skl_grid_cell(_rank_score_or_dash(current_by_rank.get(rank)), item_style, bg, (gw, gh), "r")
        for source in forecast_by_rank:
            _draw_skl_grid_cell(_rank_score_or_dash(source.get(rank)), item_style, bg, (gw, gh), "r")


def _draw_skl_prediction_footer(
    ranks: list[int],
    current_ranks: list[RankInfo],
    forecast_columns: list,
    title_style: TextStyle,
    item_style: TextStyle,
    bg1: FillBg,
    bg2: FillBg,
    gw: int,
    gh: int,
) -> None:
    footer_bg = bg2 if len(ranks) % 2 == 0 else bg1
    _draw_skl_grid_cell("预测时间", title_style, footer_bg, (gw, gh), "c")
    _draw_skl_grid_cell("-", item_style, footer_bg, (gw, gh), "c")
    for column in forecast_columns:
        _draw_skl_grid_cell(_readable_datetime_or_dash(column.forecast_time), item_style, footer_bg, (gw, gh), "c")

    update_bg = bg1 if footer_bg == bg2 else bg2
    _draw_skl_grid_cell("获取时间", title_style, update_bg, (gw, gh), "c")
    latest_current = max(current_ranks, key=lambda rank: rank.time).time if current_ranks else None
    _draw_skl_grid_cell(_readable_datetime_or_dash(latest_current), item_style, update_bg, (gw, gh), "c")
    for column in forecast_columns:
        _draw_skl_grid_cell(_readable_datetime_or_dash(column.update_time), item_style, update_bg, (gw, gh), "c")


def _draw_skl_prediction_table(
    ranks: list[int],
    current_ranks: list[RankInfo],
    forecast_columns: list,
    current_by_rank: dict[int, RankInfo],
    forecast_by_rank: list[dict[int, RankInfo]],
    title_style: TextStyle,
    item_style: TextStyle,
    bg1: FillBg,
    bg2: FillBg,
    gh: int,
) -> None:
    gw = 180
    with (
        Grid(col_count=len(forecast_columns) + 2)
        .set_content_align("c")
        .set_item_align("c")
        .set_sep(8, 5)
        .set_padding(0)
    ):
        TextBox("排名", title_style).set_bg(bg1).set_size((gw, gh)).set_content_align("c")
        TextBox("当前榜线", title_style).set_bg(bg1).set_size((gw, gh)).set_content_align("c")
        for column in forecast_columns:
            TextBox(column.name, title_style).set_bg(bg1).set_size((gw, gh)).set_content_align("c")
        _draw_skl_prediction_rows(ranks, current_by_rank, forecast_by_rank, item_style, bg1, bg2, gw, gh)
        _draw_skl_prediction_footer(
            ranks,
            current_ranks,
            forecast_columns,
            title_style,
            item_style,
            bg1,
            bg2,
            gw,
            gh,
        )


def _draw_skl_current_table(
    current_ranks: list[RankInfo],
    title_style: TextStyle,
    item_style: TextStyle,
    bg1: FillBg,
    bg2: FillBg,
    gh: int,
) -> None:
    with HSplit().set_content_align("c").set_item_align("c").set_sep(5).set_padding(0):
        TextBox("排名", title_style).set_bg(bg1).set_size((140, gh)).set_content_align("c")
        TextBox("分数", title_style).set_bg(bg1).set_size((180, gh)).set_content_align("c")
        TextBox("RT", title_style).set_bg(bg1).set_size((180, gh)).set_content_align("c")
    for index, rank in enumerate(current_ranks):
        with HSplit().set_content_align("c").set_item_align("c").set_sep(5).set_padding(0):
            bg = bg2 if index % 2 == 0 else bg1
            rank_text = get_board_rank_str(rank.rank)
            score_text = get_board_score_str(rank.score)
            rt_text = get_readable_datetime(rank.time, show_original_time=False, use_en_unit=False)
            _draw_skl_grid_cell(rank_text, item_style, bg, (140, gh), "r")
            _draw_skl_grid_cell(score_text, item_style, bg, (180, gh), "r")
            _draw_skl_grid_cell(rt_text, item_style, bg, (180, gh), "r")


def _draw_skl_data_panel(
    ranks: list[int],
    current_ranks: list[RankInfo],
    forecast_columns: list,
    prediction_notice: str | None,
) -> None:
    gh = 30
    bg1 = FillBg((255, 255, 255, 200))
    bg2 = FillBg((255, 255, 255, 100))
    title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK)
    item_style = TextStyle(font=DEFAULT_FONT, size=20, color=BLACK)
    with VSplit().set_content_align("c").set_item_align("c").set_sep(8).set_padding(8):
        if prediction_notice:
            TextBox(
                prediction_notice,
                TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(70, 70, 70, 255)),
            ).set_content_align("c").set_padding((4, 0))
        if forecast_columns:
            current_by_rank = {rank.rank: rank for rank in current_ranks}
            forecast_by_rank = [{rank.rank: rank for rank in column.ranks} for column in forecast_columns]
            _draw_skl_prediction_table(
                ranks,
                current_ranks,
                forecast_columns,
                current_by_rank,
                forecast_by_rank,
                title_style,
                item_style,
                bg1,
                bg2,
                gh,
            )
        else:
            _draw_skl_current_table(current_ranks, title_style, item_style, bg1, bg2, gh)


def get_event_id_and_name_text(region: str, event_id: int, event_name: str) -> str:
    """
    获取格式化的活动ID和名称文本

    格式:
    - 普通活动: [REGION-ID] Name
    - WL活动: [REGION-ID-第Ch章单榜] Name
    """
    if event_id < 1000:
        return f"【{region.upper()}-{event_id}】{event_name}"
    else:
        chapter_id = event_id // 1000
        event_id = event_id % 1000
        return f"【{region.upper()}-{event_id}-第{chapter_id}章单榜】{event_name}"


# 获取榜线排名字符串
def get_board_rank_str(rank: int) -> str:
    """
    格式化排名数字

    例如: 1000 -> 1,000
    """
    # 每3位加一个逗号
    return f"{rank:,}"


def draw_day_night_bg(ax, start_time: datetime, end_time: datetime):
    """
    在 Matplotlib 图表中绘制昼夜交替背景

    白天 (12:00) 偏亮，夜晚 (0:00) 偏暗
    """

    def get_time_bg_color(time: datetime) -> str:
        night_color = (200, 200, 230)  # 0:00
        day_color = (245, 245, 250)  # 12:00
        ratio = math.sin(time.hour / 24 * math.pi * 2 - math.pi / 2)
        color = lerp_color(night_color, day_color, (ratio + 1) / 2)
        return rgb_to_color_code(color)

    interval = timedelta(hours=1)
    start_time = start_time.replace(minute=0, second=0, microsecond=0)
    bg_times = [start_time]
    while bg_times[-1] < end_time:
        bg_times.append(bg_times[-1] + interval)
    bg_colors = [get_time_bg_color(t) for t in bg_times]
    for i in range(len(bg_times)):
        start = bg_times[i]
        end = min(bg_times[i] + interval, end_time)
        if end <= start:
            continue
        ax.axvspan(start, end, facecolor=bg_colors[i], edgecolor=None, zorder=0)


# 获取榜线分数字符串
def get_board_score_str(score: int, width: int | None = None) -> str:
    """
    格式化分数字符串

    例如: 123456 -> 12.3456w
    """
    if score is None:
        ret = "?"
    else:
        score = int(score)
        M = 10000
        ret = f"{score // M}.{score % M:04d}w"
    if width:
        ret = ret.rjust(width)
    return ret


async def _build_skl_canvas(rqd: SklRequest) -> Canvas:
    """
    合成通过排名列表图片 (SKL)

    Args:
        rqd: 请求数据
    """
    event_start = datetime_from_millis(rqd.start_at, rqd.timezone)
    event_end = datetime_from_millis(rqd.aggregate_at + 1000, rqd.timezone)
    now = request_now(rqd.timezone)
    banner_img = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.banner_img_path)
    wl_chara_img = None
    if rqd.wl_cid:
        wl_chara_img = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.chara_icon_path)

    forecast_columns = list(rqd.forecast_columns or [])
    prediction_notice = rqd.prediction_notice
    if forecast_columns and not prediction_notice:
        prediction_notice = _DEFAULT_PREDICTION_NOTICE
    current_ranks = list(rqd.current_ranks or rqd.ranks)
    ranks = _collect_skl_display_ranks(current_ranks, forecast_columns)

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_item_bg(roundrect_bg(alpha=80)):
            _draw_skl_header(rqd, event_start, event_end, now, banner_img, wl_chara_img)
            if ranks:
                _draw_skl_data_panel(ranks, current_ranks, forecast_columns, prediction_notice)
            else:
                TextBox("暂无榜线数据", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)).set_padding(32)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_skl_image(rqd: SklRequest) -> Image.Image:
    return await (await _build_skl_canvas(rqd)).get_img()


async def try_render_skl_payload(rqd: SklRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_skl_canvas(rqd), endpoint="sk_line")


# 合成榜线查询图片
async def _build_sk_canvas(rqd: SKRequest) -> Canvas:
    """
    合成活动排名查询结果图片 (SK/SKK)

    展示特定排名的分数、RT、时速以及前后排名的分差
    """
    eid = rqd.id
    title = rqd.name
    event_end = datetime_from_millis(rqd.aggregate_at + 1000, rqd.timezone)
    now = request_now(rqd.timezone)
    if rqd.wl_chara_icon_path:
        wl_chara_img = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.wl_chara_icon_path)

    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)
    style2 = TextStyle(font=DEFAULT_FONT, size=24, color=BLACK)
    style3 = TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=BLACK)
    texts: list[str, TextStyle] = []

    ranks = rqd.ranks
    ranks.sort(key=lambda x: x.rank)

    # 查询单个
    if len(ranks) == 1:
        rank = ranks[0]
        texts.append((f"{truncate(rank.name, 40)}", style2))
        texts.append((f"排名 {get_board_rank_str(rank.rank)} - 分数 {get_board_score_str(rank.score)}", style3))
        if prev_rank := rqd.prev_ranks:
            dlt_score = prev_rank.score - rank.score
            texts.append(
                (
                    f"{prev_rank.rank}名分数: {get_board_score_str(prev_rank.score)}  "
                    f"↑{get_board_score_str(dlt_score)}",
                    style2,
                )
            )
        if next_rank := rqd.next_ranks:
            dlt_score = rank.score - next_rank.score
            texts.append(
                (
                    f"{next_rank.rank}名分数: {get_board_score_str(next_rank.score)}  "
                    f"↓{get_board_score_str(dlt_score)}",
                    style2,
                )
            )
        texts.append((f"RT: {get_readable_datetime(rank.time, show_original_time=False)}", style2))
    # 查询多个
    else:
        for rank in rqd.ranks:
            texts.append((truncate(rank.name, 40), style1))
            texts.append((f"排名 {get_board_rank_str(rank.rank)} - 分数 {get_board_score_str(rank.score)}", style2))
            texts.append((f"RT: {get_readable_datetime(rank.time, show_original_time=False)}", style2))

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_item_bg(roundrect_bg(alpha=80)):
            with HSplit().set_content_align("rt").set_item_align("rt").set_padding(8).set_sep(7):
                with VSplit().set_content_align("lt").set_item_align("lt").set_sep(5):
                    TextBox(
                        get_event_id_and_name_text(rqd.region, eid, truncate(title, 20)),
                        TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK),
                    )
                    time_to_end = event_end - now
                    if time_to_end.total_seconds() <= 0:
                        time_to_end = _EVENT_ENDED_TEXT
                    else:
                        time_to_end = f"距离活动结束还有{get_readable_timedelta(time_to_end)}"
                    TextBox(time_to_end, TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                if rqd.wl_chara_icon_path is not None:
                    ImageBox(wl_chara_img, size=(None, 50))

            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(6).set_padding(16):
                for text, style in texts:
                    TextBox(text, style)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_sk_image(rqd: SKRequest) -> Image.Image:
    return await (await _build_sk_canvas(rqd)).get_img(1.5)


async def try_render_sk_payload(rqd: SKRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_sk_canvas(rqd), endpoint="sk_query", scale=1.5)


def _first_non_empty(*values: str | None, fallback: str) -> str:
    for value in values:
        if value is not None and (stripped := value.strip()):
            return stripped
    return fallback


def _optional_text(value, *, format_spec: str = "") -> str:
    if value is None:
        return "?"
    return format(value, format_spec)


def _cf_record_start_text(rank: RankInfo) -> str:
    if rank.record_start_at is None:
        return "未知"
    return get_readable_datetime(rank.record_start_at, show_original_time=False)


def _cf_neighbor_text(rank: RankInfo, neighbor: RankInfo, direction: str) -> str:
    neighbor_score = get_board_score_str(neighbor.score)
    if neighbor.score is None or rank.score is None:
        score_gap = "?"
    elif direction == "↑":
        score_gap = get_board_score_str(neighbor.score - rank.score)
    else:
        score_gap = get_board_score_str(rank.score - neighbor.score)
    return f"{neighbor.rank}名分数: {neighbor_score}  {direction}{score_gap}"


def _build_cf_single_texts(
    rqd: CFRequest,
    rank: RankInfo,
    request_player_name: str,
    style1: TextStyle,
    style2: TextStyle,
    style3: TextStyle,
) -> list[StyledText]:
    player_title = _first_non_empty(request_player_name, rank.name, fallback=rqd.event_name)
    average_round = _optional_text(rank.average_round)
    texts: list[StyledText] = [
        (player_title, style1),
        (f"当前排名 {rank.rank} - 当前分数 {get_board_score_str(rank.score)}", style2),
    ]
    if rqd.prev_rank is not None:
        texts.append((_cf_neighbor_text(rank, rqd.prev_rank, "↑"), style3))
    if rqd.next_rank is not None:
        texts.append((_cf_neighbor_text(rank, rqd.next_rank, "↓"), style3))
    texts.extend(
        [
            (f"近{average_round}次平均Pt: {_optional_text(rank.average_pt, format_spec='.1f')}", style2),
            (f"最近一次Pt: {_optional_text(rank.latest_pt)}", style2),
            (f"时速: {get_board_score_str(rank.speed)}", style2),
        ]
    )
    if rank.min20_times_3_speed is not None:
        texts.append((f"20min×3时速: {get_board_score_str(rank.min20_times_3_speed)}", style2))
    texts.extend(
        [
            (f"本小时周回数: {_optional_text(rank.hour_round)}", style2),
            (f"数据开始于: {_cf_record_start_text(rank)}", style2),
            (f"数据更新于: {get_readable_datetime(rqd.update_at, show_original_time=False)}", style2),
        ]
    )
    return texts


def _build_cf_multi_rank_texts(
    rqd: CFRequest,
    rank: RankInfo,
    request_player_name: str,
    style1: TextStyle,
    style2: TextStyle,
) -> list[StyledText]:
    player_title = _first_non_empty(rank.name, request_player_name, fallback=rqd.event_name)
    average_round = _optional_text(rank.average_round)
    return [
        (player_title, style1),
        (f"当前排名 {get_board_rank_str(rank.rank)} - 当前分数 {get_board_score_str(rank.score)}", style2),
        (
            f"时速: {get_board_score_str(rank.speed)} - "
            f"近{average_round}次平均Pt: {_optional_text(rank.average_pt, format_spec='.1f')}",
            style2,
        ),
        (f"本小时周回数: {_optional_text(rank.hour_round)}", style2),
        (
            f"RT: {_cf_record_start_text(rank)} ~ "
            f"{get_readable_datetime(rqd.update_at, show_original_time=False, use_en_unit=False)}",
            style2,
        ),
    ]


def _build_cf_texts(rqd: CFRequest, style1: TextStyle, style2: TextStyle, style3: TextStyle) -> list[StyledText]:
    request_player_name = (rqd.name or rqd.username or "").strip()
    if len(rqd.ranks) == 1:
        return _build_cf_single_texts(rqd, rqd.ranks[0], request_player_name, style1, style2, style3)
    return [
        text
        for rank in rqd.ranks
        for text in _build_cf_multi_rank_texts(rqd, rank, request_player_name, style1, style2)
    ]


def _draw_query_header(
    region: str,
    event_id: int,
    title: str,
    event_end: datetime,
    now: datetime,
    wl_chara_img,
    *,
    show_icon: bool,
    extra_lines: tuple[StyledText, ...] = (),
) -> None:
    with HSplit().set_content_align("rt").set_item_align("rt").set_padding(8).set_sep(7):
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(5):
            TextBox(
                get_event_id_and_name_text(region, event_id, truncate(title, 20)),
                TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK),
            )
            TextBox(
                _time_to_event_end_text(event_end, now),
                TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK),
            )
            for text, style in extra_lines:
                TextBox(text, style)
        if show_icon:
            ImageBox(wl_chara_img, size=(None, 50))


# 合成查房图片
async def _build_cf_canvas(rqd: CFRequest) -> Canvas:
    """
    合成查房结果图片 (CF)

    展示特定玩家的实时排名、分数、时速、周回数等详细数据
    """
    event_end = datetime_from_millis(rqd.aggregate_at + 1000, rqd.timezone)
    now = request_now(rqd.timezone)
    wl_chara_img = None
    if rqd.wl_chara_icon_path:
        wl_chara_img = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.wl_chara_icon_path)

    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)
    style2 = TextStyle(font=DEFAULT_FONT, size=24, color=BLACK)
    style3 = TextStyle(font=DEFAULT_FONT, size=20, color=BLACK)
    texts = _build_cf_texts(rqd, style1, style2, style3)

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_item_bg(roundrect_bg(alpha=80)):
            _draw_query_header(
                rqd.region,
                rqd.eid,
                rqd.event_name,
                event_end,
                now,
                wl_chara_img,
                show_icon=bool(rqd.wl_chara_icon_path),
            )
            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(6).set_padding(16):
                for text, style in texts:
                    TextBox(text, style)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_cf_image(rqd: CFRequest) -> Image.Image:
    return await (await _build_cf_canvas(rqd)).get_img(1.5)


async def try_render_cf_payload(rqd: CFRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_cf_canvas(rqd), endpoint="sk_check_room", scale=1.5)


def _collect_csb_heat_counts(ranks: list[RankInfo]) -> tuple[date, list[list[int]], list[list[int]]]:
    rankcounts: list[list[int]] = []
    playcounts: list[list[int]] = []
    start_date = ranks[0].time.date()
    for current, following in pairwise(ranks):
        day = (current.time.date() - start_date).days
        while len(rankcounts) <= day:
            rankcounts.append([0] * 24)
            playcounts.append([0] * 24)
        hour = current.time.hour
        rankcounts[day][hour] += 1
        if (following.score or 0) > (current.score or 0):
            playcounts[day][hour] += 1
    return start_date, rankcounts, playcounts


def _collect_csb_stop_segments(ranks: list[RankInfo]) -> list[tuple[RankInfo, RankInfo]]:
    stop_segments: list[tuple[RankInfo, RankInfo]] = []
    left = None
    right = None
    for rank in ranks:
        left = left or rank
        right = right or rank
        should_split = rank.rank > 100 or rank.time - right.time > SK_RECORD_TOLERANCE
        score_changed = (rank.score or 0) != (right.score or 0)
        if should_split or score_changed:
            if left != right:
                stop_segments.append((left, right))
            left, right = rank, None
        else:
            right = rank
    if left is not None and right is not None:
        stop_segments.append((left, right))
    return stop_segments


def _mark_csb_stop_hours(
    stop_hours: list[list[bool]],
    start_date: date,
    start_time: datetime,
    end_time: datetime,
) -> None:
    hour_cursor = start_time.replace(minute=0, second=0, microsecond=0)
    end_hour = end_time.replace(minute=0, second=0, microsecond=0)
    while hour_cursor <= end_hour:
        day = (hour_cursor.date() - start_date).days
        while len(stop_hours) <= day:
            stop_hours.append([False] * 24)
        stop_hours[day][hour_cursor.hour] = True
        hour_cursor += timedelta(hours=1)


def _build_csb_stop_texts(
    latest_rank: RankInfo,
    latest_name: str,
    stop_segments: list[tuple[RankInfo, RankInfo]],
    stop_hours: list[list[bool]],
    start_date: date,
    style1: TextStyle,
    style2: TextStyle,
) -> list[StyledText]:
    stop_texts: list[StyledText] = [(f'T{latest_rank.rank} "{latest_name}" 的停车区间', style1)]
    for left_rank, right_rank in stop_segments:
        duration = right_rank.time - left_rank.time
        if left_rank == right_rank or duration < SK_CSB_STOP_THRESHOLD:
            continue
        _mark_csb_stop_hours(stop_hours, start_date, left_rank.time, right_rank.time)
        start_text = left_rank.time.strftime("%m-%d %H:%M")
        end_text = right_rank.time.strftime("%m-%d %H:%M")
        stop_texts.append((f"{start_text} ~ {end_text}（{get_readable_timedelta(duration)}）", style2))
    if len(stop_texts) == 1:
        stop_texts.append(("未找到停车区间", style2))
    return stop_texts


def _draw_csb_heat_cell(playcount: int, rankcount: int, stopped: bool) -> None:
    if rankcount < 10:
        Spacer(w=30, h=30)
        return
    label = f"{playcount}{'*' if stopped else ''}"
    if playcount > SK_PLAYCOUNT_MYSEKAI_THRESHOLD:
        color = (204, 255, 204)
    else:
        color = lerp_color(
            (184, 216, 255),
            (255, 181, 181),
            max(min((playcount - 15) / 15, 1.0), 0.0),
        )
    TextBox(label, TextStyle(font=DEFAULT_FONT, size=14, color=BLACK), overflow="clip").set_bg(
        roundrect_bg(fill=color, radius=4)
    ).set_content_align("c").set_size((30, 30)).set_offset((0, -2))


def _draw_csb_heatmap(
    latest_rank: RankInfo,
    latest_name: str,
    rankcounts: list[list[int]],
    playcounts: list[list[int]],
    stop_hours: list[list[bool]],
    heat_title_style: TextStyle,
    heat_hint_style: TextStyle,
) -> None:
    with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_padding(16):
        TextBox(f'T{latest_rank.rank} "{latest_name}" 各小时Pt变化次数', heat_title_style)
        TextBox("标注*号的小时存在停车区间", heat_hint_style)
        with Grid(col_count=24).set_sep(1, 1):
            for hour in range(24):
                TextBox(str(hour), TextStyle(font=DEFAULT_FONT, size=12, color=BLACK)).set_content_align("c").set_size(
                    (30, 30)
                )
            for day, day_rankcounts in enumerate(rankcounts):
                for hour, rankcount in enumerate(day_rankcounts):
                    stopped = day < len(stop_hours) and stop_hours[day][hour]
                    _draw_csb_heat_cell(playcounts[day][hour], rankcount, stopped)


def _draw_csb_stop_panel(stop_texts: list[StyledText]) -> None:
    row_num = len(stop_texts) // 2 + 1
    first_text = stop_texts[0]
    left_texts = stop_texts[1:row_num]
    right_texts = stop_texts[row_num:]
    with VSplit().set_content_align("lt").set_item_align("lt").set_sep(6).set_padding(16):
        TextBox(*first_text)
        with HSplit().set_content_align("lt").set_item_align("lt").set_sep(12):
            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(4):
                for text in left_texts:
                    TextBox(*text)
            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(4):
                for text in right_texts:
                    TextBox(*text)


async def _build_csb_canvas(rqd: CSBRequest) -> tuple[Canvas, float]:
    """
    合成查水表热力图图片 (CSB)

    展示玩家各小时 Pt 变化次数热力图以及停车区间
    """
    event_end = datetime_from_millis(rqd.aggregate_at + 1000, rqd.timezone)
    now = request_now(rqd.timezone)
    wl_chara_img = None
    if rqd.wl_chara_icon_path:
        wl_chara_img = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.wl_chara_icon_path)

    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)
    style2 = TextStyle(font=DEFAULT_FONT, size=20, color=BLACK)
    heat_title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=BLACK)
    heat_hint_style = TextStyle(font=DEFAULT_FONT, size=18, color=BLACK)

    ranks = sorted(rqd.ranks, key=lambda item: item.time)
    latest_rank = ranks[-1]
    latest_name = truncate(latest_rank.name, 40)
    start_date, rankcounts, playcounts = _collect_csb_heat_counts(ranks)
    stop_segments = _collect_csb_stop_segments(ranks)
    stop_hours: list[list[bool]] = [[False] * 24 for _ in range(len(rankcounts))]
    stop_texts = _build_csb_stop_texts(
        latest_rank,
        latest_name,
        stop_segments,
        stop_hours,
        start_date,
        style1,
        style2,
    )
    update_text = get_readable_datetime(rqd.update_at, show_original_time=False, use_en_unit=False)
    update_line = ((f"数据更新于: {update_text}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK)),)

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_item_bg(roundrect_bg(alpha=80)):
            _draw_query_header(
                rqd.region,
                rqd.eid,
                rqd.event_name,
                event_end,
                now,
                wl_chara_img,
                show_icon=bool(rqd.wl_chara_icon_path),
                extra_lines=update_line,
            )
            _draw_csb_heatmap(
                latest_rank,
                latest_name,
                rankcounts,
                playcounts,
                stop_hours,
                heat_title_style,
                heat_hint_style,
            )
            _draw_csb_stop_panel(stop_texts)

    add_request_watermark(canvas, rqd)
    return canvas, (1.5 if len(stop_texts) < 10 else 1.0)


async def compose_csb_image(rqd: CSBRequest) -> Image.Image:
    canvas, scale = await _build_csb_canvas(rqd)
    return await canvas.get_img(scale)


async def try_render_csb_payload(rqd: CSBRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    canvas, scale = await _build_csb_canvas(rqd)
    return await render_canvas_payload(canvas, endpoint="sk_csb", scale=scale)


async def _build_sks_canvas(rqd: SpeedRequest) -> Canvas:
    """
    合成时速分析图片 (SKS)

    展示各档位的实时时速排名
    """
    unit_text = rqd.request_type
    eid = rqd.event_id
    title = rqd.event_name
    event_start = datetime_from_millis(rqd.event_start_at, rqd.timezone)
    event_end = datetime_from_millis(rqd.event_aggregate_at + 1000, rqd.timezone)
    now = request_now(rqd.timezone)
    banner_img = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.banner_img_path)
    is_wl_event = rqd.is_wl_event
    period = rqd.period
    speeds = _collect_speed_display_rows(rqd.ranks)
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_item_bg(roundrect_bg(alpha=80)):
            with HSplit().set_content_align("rt").set_item_align("rt").set_padding(8).set_sep(7):
                with VSplit().set_content_align("lt").set_item_align("lt").set_sep(5):
                    TextBox(
                        get_event_id_and_name_text(rqd.region, eid, truncate(title, 16)),
                        TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK),
                    )
                    TextBox(
                        f"{event_start.strftime('%Y-%m-%d %H:%M')} ~ {event_end.strftime('%Y-%m-%d %H:%M')}",
                        TextStyle(font=DEFAULT_FONT, size=18, color=BLACK),
                    )
                    time_to_end = event_end - now
                    if time_to_end.total_seconds() <= 0:
                        time_to_end = _EVENT_ENDED_TEXT
                    else:
                        time_to_end = f"距离活动结束还有{get_readable_timedelta(time_to_end)}"
                    TextBox(time_to_end, TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                with Frame().set_content_align("r"):
                    if banner_img:
                        ImageBox(banner_img, size=(140, None))
                    if is_wl_event:
                        ImageBox(await get_asset_image_ref(ASSETS_BASE_DIR, rqd.wl_chara_icon_path), size=(None, 50))

            if speeds:
                gh = 30
                bg1 = FillBg((255, 255, 255, 200))
                bg2 = FillBg((255, 255, 255, 100))
                title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK)
                item_style = TextStyle(font=DEFAULT_FONT, size=20, color=BLACK)
                with VSplit().set_content_align("l").set_item_align("l").set_sep(8).set_padding(8):
                    TextBox(f"近{get_readable_timedelta(period)}换算{unit_text}速", title_style).set_size(
                        (420, None)
                    ).set_padding((8, 8))

                    with HSplit().set_content_align("c").set_item_align("c").set_sep(5).set_padding(0):
                        TextBox("排名", title_style).set_bg(bg1).set_size((120, gh)).set_content_align("c")
                        TextBox("分数", title_style).set_bg(bg1).set_size((180, gh)).set_content_align("c")
                        TextBox(f"{unit_text}速", title_style).set_bg(bg1).set_size((140, gh)).set_content_align("c")
                        TextBox("RT", title_style).set_bg(bg1).set_size((160, gh)).set_content_align("c")
                    for i, (rank, score, speed, rt) in enumerate(speeds):
                        with HSplit().set_content_align("c").set_item_align("c").set_sep(5).set_padding(0):
                            bg = bg2 if i % 2 == 0 else bg1
                            r = get_board_rank_str(rank)
                            speed = get_board_score_str(speed) if speed is not None else "-"
                            score = get_board_score_str(score)
                            rt = get_readable_datetime(rt, show_original_time=False, use_en_unit=False)
                            TextBox(r, item_style, overflow="clip").set_bg(bg).set_size((120, gh)).set_content_align(
                                "r"
                            ).set_padding((16, 0))
                            TextBox(score, item_style, overflow="clip").set_bg(bg).set_size(
                                (180, gh)
                            ).set_content_align("r").set_padding((16, 0))
                            TextBox(
                                speed,
                                item_style,
                            ).set_bg(bg).set_size((140, gh)).set_content_align("r").set_padding((8, 0))
                            TextBox(rt, item_style, overflow="clip").set_bg(bg).set_size((160, gh)).set_content_align(
                                "r"
                            ).set_padding((16, 0))
            else:
                TextBox("暂无时速数据", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)).set_padding(32)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_sks_image(rqd: SpeedRequest) -> Image.Image:
    return await (await _build_sks_canvas(rqd)).get_img()


async def try_render_sks_payload(rqd: SpeedRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_sks_canvas(rqd), endpoint="sk_speed")


@dataclass(frozen=True)
class _PlayerTraceSeries:
    name: str
    times: list[datetime]
    scores: list[int]
    ranks: list[int]


@dataclass(frozen=True)
class _ScoreTraceSeries:
    times: list[datetime]
    scores: list[int]


def _prepare_player_trace_series(ranks: list[RankInfo] | None) -> _PlayerTraceSeries | None:
    visible_ranks = [rank for rank in ranks or [] if rank.rank <= 100]
    if not visible_ranks:
        return None
    visible_ranks.sort(key=lambda rank: rank.time)
    return _PlayerTraceSeries(
        name=truncate(visible_ranks[-1].name, 40),
        times=[rank.time for rank in visible_ranks],
        scores=[rank.score for rank in visible_ranks],
        ranks=[rank.rank for rank in visible_ranks],
    )


def _prepare_score_trace_series(ranks: list[RankInfo] | None) -> tuple[_ScoreTraceSeries | None, int | None]:
    score_ranks = [rank for rank in ranks or [] if rank.score is not None]
    if not score_ranks:
        return None, None
    score_ranks.sort(key=lambda rank: rank.time)
    return (
        _ScoreTraceSeries(
            times=[rank.time for rank in score_ranks],
            scores=[rank.score for rank in score_ranks],
        ),
        score_ranks[-1].rank,
    )


def _resolve_player_trace_reference(
    rqd: PlayerTraceRequest,
    compare_series: _ScoreTraceSeries | None,
    fallback_time: datetime,
) -> tuple[int | None, datetime]:
    line_score = rqd.compare_rank_line_score
    line_time = None
    if line_score is None and rqd.compare_rank_latest is not None:
        line_score = rqd.compare_rank_latest.score
    if rqd.compare_rank_latest is not None:
        line_time = rqd.compare_rank_latest.time
    if line_score is None and compare_series is not None:
        line_score = compare_series.scores[-1]
    if line_time is None and compare_series is not None:
        line_time = compare_series.times[-1]
    return line_score, line_time or fallback_time


def _player_trace_bounds(
    primary: _PlayerTraceSeries,
    secondary: _PlayerTraceSeries | None,
    compare_series: _ScoreTraceSeries | None,
    compare_line_score: int | None,
) -> tuple[int, int]:
    score_groups = [primary.scores]
    if secondary is not None:
        score_groups.append(secondary.scores)
    if compare_series is not None:
        score_groups.append(compare_series.scores)
    values = [score for group in score_groups for score in group]
    if compare_line_score is not None and compare_series is None:
        values.append(compare_line_score)
    return min(values), max(values)


def _draw_player_score_series(ax, series: _PlayerTraceSeries, colors: tuple[str, str], lines: list) -> None:
    (line_score,) = ax.plot(
        series.times,
        series.scores,
        "o",
        label=f"{series.name}分数",
        color=colors[0],
        markersize=1,
        linewidth=0.5,
    )
    lines.append(line_score)
    ax.annotate(
        get_board_score_str(series.scores[-1]),
        xy=(series.times[-1], series.scores[-1]),
        xytext=(series.times[-1], series.scores[-1]),
        color=colors[0],
        fontsize=12,
        ha="right",
        path_effects=PLOT_LABEL_PATH_EFFECTS,
    )


def _draw_compare_score_series(
    ax,
    series: _ScoreTraceSeries,
    compare_rank: int | None,
    lines: list,
) -> None:
    compare_label = f"T{compare_rank}分数线" if compare_rank else "参考分数线"
    (line_compare_score,) = ax.plot(
        series.times,
        series.scores,
        "o",
        label=compare_label,
        color="dimgray",
        markersize=1,
        linewidth=0.5,
        linestyle="--",
        alpha=0.85,
    )
    lines.append(line_compare_score)
    ax.annotate(
        f"{compare_label} {get_board_score_str(series.scores[-1])}",
        xy=(series.times[-1], series.scores[-1]),
        xytext=(series.times[-1], series.scores[-1]),
        color="dimgray",
        fontsize=12,
        ha="right",
        path_effects=PLOT_LABEL_PATH_EFFECTS,
    )


def _draw_player_reference_line(
    ax,
    compare_rank: int | None,
    compare_line_score: int,
    compare_line_time: datetime,
    lines: list,
) -> None:
    line_label = f"T{compare_rank}当前" if compare_rank else "参考当前"
    line_latest = ax.axhline(
        y=compare_line_score,
        color="gray",
        linestyle=":",
        linewidth=0.8,
        alpha=0.9,
        label=line_label,
    )
    lines.append(line_latest)
    ax.text(
        compare_line_time,
        compare_line_score,
        f"{line_label}: {get_board_score_str(compare_line_score)}",
        color="gray",
        fontsize=12,
        ha="right",
        va="bottom",
        path_effects=PLOT_LABEL_PATH_EFFECTS,
    )


def _draw_player_rank_series(ax, series: _PlayerTraceSeries, color: str, lines: list) -> None:
    (line_rank,) = ax.plot(
        series.times,
        series.ranks,
        "o",
        label=f"{series.name}排名",
        color=color,
        markersize=0.7,
        linewidth=0.5,
    )
    lines.append(line_rank)
    ax.annotate(
        str(int(series.ranks[-1])),
        xy=(series.times[-1], series.ranks[-1] * 1.02),
        xytext=(series.times[-1], series.ranks[-1] * 1.02),
        color=color,
        fontsize=12,
        ha="right",
        path_effects=PLOT_LABEL_PATH_EFFECTS,
    )


def _player_trace_title(
    rqd: PlayerTraceRequest,
    primary: _PlayerTraceSeries,
    secondary: _PlayerTraceSeries | None,
) -> str:
    prefix = get_event_id_and_name_text(rqd.region, rqd.event_id, "")
    if secondary is None:
        return f"{prefix} 玩家: {primary.name}"
    return f"{prefix} 玩家: {primary.name} vs {secondary.name}"


def _render_player_trace_plot(
    rqd: PlayerTraceRequest,
    primary: _PlayerTraceSeries,
    secondary: _PlayerTraceSeries | None,
    compare_series: _ScoreTraceSeries | None,
    compare_rank: int | None,
    compare_line_score: int | None,
    compare_line_time: datetime,
    plot_start: datetime,
    plot_end: datetime,
) -> Image.Image:
    fig = Figure(figsize=(12, 8))
    ax = fig.add_subplot(111)
    try:
        fig.subplots_adjust(wspace=0, hspace=0)
        draw_day_night_bg(ax, plot_start, plot_end)
        min_score, max_score = _player_trace_bounds(primary, secondary, compare_series, compare_line_score)
        lines = []
        _draw_player_score_series(ax, primary, ("royalblue", "cornflowerblue"), lines)
        if secondary is not None:
            _draw_player_score_series(ax, secondary, ("orangered", "coral"), lines)
        if compare_series is not None:
            _draw_compare_score_series(ax, compare_series, compare_rank, lines)
        if compare_line_score is not None and compare_series is None:
            _draw_player_reference_line(ax, compare_rank, compare_line_score, compare_line_time, lines)

        ax.set_ylim(min_score * 0.95, max_score * 1.05)
        ax.set_xlim(plot_start, plot_end)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: get_board_score_str(x)))
        ax.grid(True, linestyle="-", alpha=0.3, color="gray")
        ax2 = ax.twinx()
        _draw_player_rank_series(ax2, primary, "cornflowerblue", lines)
        if secondary is not None:
            _draw_player_rank_series(ax2, secondary, "coral", lines)
        ax2.yaxis.set_major_formatter(FuncFormatter(lambda x, _: str(int(x)) if 1 <= int(x) <= 100 else ""))
        ax2.set_ylim(110, -10)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M", tz=plot_start.tzinfo))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        fig.autofmt_xdate()
        ax.set_title(_player_trace_title(rqd, primary, secondary))
        labels = [line.get_label() for line in lines]
        legend = ax2.legend(lines, labels, loc="upper left")
        legend.set_zorder(1000)
        return plt_fig_to_image(fig)
    finally:
        fig.clear()


def _draw_trace_canvas(img: Image.Image, wl_chara_icon, *, show_icon: bool) -> Canvas:
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        ImageBox(img).set_bg(roundrect_bg(fill=(255, 255, 255, 200)))
        if show_icon:
            with (
                VSplit()
                .set_content_align("c")
                .set_item_align("c")
                .set_sep(4)
                .set_bg(roundrect_bg(alpha=80))
                .set_padding(8)
            ):
                ImageBox(wl_chara_icon, size=(None, 50))
                TextBox("单榜", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK))
    return canvas


async def _build_player_trace_canvas(rqd: PlayerTraceRequest) -> Canvas:
    """
    合成玩家排名追踪图表 (Rating Trace)

    使用 Matplotlib 绘制双轴图表：
    - 左轴: 分数折线图
    - 右轴: 排名散点图

    matplotlib renders the plot bitmap; the surrounding chrome (rounded card, WL
    icon column, watermark) is a plot.py widget tree the Skia/IRPainter path can
    render, shipping the bitmap as a mem-image.
    """
    wl_chara_icon = None
    if rqd.wl_chara_icon_path:
        wl_chara_icon = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.wl_chara_icon_path)

    primary = _prepare_player_trace_series(rqd.ranks)
    if primary is None:
        raise ValueError("player trace requires at least one rank entry within top 100")
    secondary = _prepare_player_trace_series(rqd.ranks2)
    compare_series, fallback_compare_rank = _prepare_score_trace_series(rqd.compare_rank_trace)
    compare_rank = rqd.compare_rank or fallback_compare_rank
    compare_line_score, compare_line_time = _resolve_player_trace_reference(
        rqd,
        compare_series,
        primary.times[-1],
    )

    plot_times = list(primary.times)
    if secondary is not None:
        plot_times.extend(secondary.times)
    if compare_series is not None:
        plot_times.extend(compare_series.times)
    plot_start = min(plot_times)
    plot_end = max(plot_times)

    img = await run_matplotlib_plot(
        lambda: _render_player_trace_plot(
            rqd,
            primary,
            secondary,
            compare_series,
            compare_rank,
            compare_line_score,
            compare_line_time,
            plot_start,
            plot_end,
        )
    )
    canvas = _draw_trace_canvas(img, wl_chara_icon, show_icon=wl_chara_icon is not None)
    add_request_watermark(canvas, rqd)
    return canvas


async def compose_player_trace_image(rqd: PlayerTraceRequest) -> Image.Image:
    return await (await _build_player_trace_canvas(rqd)).get_img()


async def try_render_player_trace_payload(rqd: PlayerTraceRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_player_trace_canvas(rqd), endpoint="sk_player_trace")


def _calculate_rank_trace_speeds(ranks: list[RankInfo]) -> list[float]:
    speeds: list[float] = []
    min_period = timedelta(minutes=50)
    max_period = timedelta(minutes=60)
    left = 0
    for right, rank in enumerate(ranks):
        while rank.time - ranks[left].time > max_period:
            left += 1
        period = rank.time - ranks[left].time
        if min_period <= period <= max_period:
            speeds.append((rank.score - ranks[left].score) / period.total_seconds() * 3600)
        else:
            speeds.append(-1)
    return speeds


def _rank_trace_point_colors(ranks: list[RankInfo]) -> list[str]:
    original_names = [rank.name for rank in ranks]
    unique_names = list(dict.fromkeys(original_names))
    if len(unique_names) > len(RANK_TRACE_SCORE_COLORS):
        return [RANK_TRACE_SCORE_COLORS[0] for _ in ranks]
    name_to_color = {name: RANK_TRACE_SCORE_COLORS[index] for index, name in enumerate(unique_names)}
    return [name_to_color[name] for name in original_names]


def _draw_rank_trace_prediction(ax, times: list[datetime], final_score: int) -> None:
    ax.axhline(y=final_score, color="red", linestyle="--", linewidth=0.5)
    ax.text(
        times[-1],
        final_score * 1.02,
        f"预测最终: {get_board_score_str(final_score)}",
        color="red",
        fontsize=12,
        ha="right",
    )


def _render_rank_trace_plot(
    rqd: RankTraceRequest,
    times: list[datetime],
    scores: list[int],
    speeds: list[float],
    point_colors: list[str],
    final_score: int | None,
) -> Image.Image:
    fig = Figure(figsize=(12, 8))
    ax = fig.add_subplot(111)
    try:
        fig.subplots_adjust(wspace=0, hspace=0)
        draw_day_night_bg(ax, times[0], times[-1])
        score_points = ax.scatter(times, scores, c=point_colors, s=3, label="分数线", zorder=3)
        ax.annotate(
            get_board_score_str(scores[-1]),
            xy=(times[-1], scores[-1]),
            xytext=(times[-1], scores[-1]),
            color=point_colors[-1],
            fontsize=12,
            ha="right",
            path_effects=PLOT_LABEL_PATH_EFFECTS,
        )
        if final_score is not None:
            _draw_rank_trace_prediction(ax, times, final_score)

        ax2 = ax.twinx()
        (line_speeds,) = ax2.plot(times, speeds, "o", label="时速", color="green", markersize=0.5, linewidth=0.5)
        ax2.yaxis.set_major_formatter(FuncFormatter(lambda x, _: get_board_score_str(int(x)) + "/h"))
        valid_speeds = [speed for speed in speeds if speed >= 0]
        max_speed = max(valid_speeds) if valid_speeds else 1
        ax2.set_ylim(0, max_speed * 1.2)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M", tz=times[0].tzinfo))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        fig.autofmt_xdate()
        ax.set_title(f"{get_event_id_and_name_text(rqd.region, rqd.event_id, '')} T{rqd.target_rank} 分数线")
        lines = [score_points, line_speeds]
        labels = [line.get_label() for line in lines]
        legend = ax2.legend(lines, labels, loc="upper left")
        legend.set_zorder(1000)
        return plt_fig_to_image(fig)
    finally:
        fig.clear()


# 合成排名追踪图片
async def _build_rank_trace_canvas(rqd: RankTraceRequest) -> Canvas:
    """
    合成排名档位追踪与预测图表

    分析特定档位的分数增长趋势，并根据预测分绘制参考线
    """
    if not rqd.ranks:
        raise ValueError("ranks must not be empty")
    rqd.ranks.sort(key=lambda rank: rank.time)
    times = [rank.time for rank in rqd.ranks]
    scores = [rank.score for rank in rqd.ranks]
    speeds = _calculate_rank_trace_speeds(rqd.ranks)
    point_colors = _rank_trace_point_colors(rqd.ranks)
    final_score = rqd.predict_ranks.score if rqd.predict_ranks is not None else None

    wl_chara_icon = None
    if rqd.wl_chara_icon_path:
        wl_chara_icon = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.wl_chara_icon_path)

    img = await run_matplotlib_plot(
        lambda: _render_rank_trace_plot(
            rqd,
            times,
            scores,
            speeds,
            point_colors,
            final_score,
        )
    )
    canvas = _draw_trace_canvas(
        img,
        wl_chara_icon,
        show_icon=rqd.wl_chara_icon_path is not None,
    )
    add_request_watermark(canvas, rqd)
    return canvas


async def compose_rank_trace_image(rqd: RankTraceRequest) -> Image.Image:
    return await (await _build_rank_trace_canvas(rqd)).get_img()


async def try_render_rank_trace_payload(rqd: RankTraceRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_rank_trace_canvas(rqd), endpoint="sk_rank_trace")


async def _build_winrate_predict_canvas(rqd: WinRateRequest) -> Canvas:
    """
    合成团队战胜率预测图片

    展示红白两队的预测胜率、是否急募等信息
    """
    eid = rqd.event_id
    banner_img = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.banner_img_path)

    event_name = rqd.event_name
    event_start = datetime_from_millis(rqd.event_start_at, rqd.timezone)
    event_end = datetime_from_millis(rqd.event_aggregate_at + 1000, rqd.timezone)
    now = request_now(rqd.timezone)

    # Build display data without mutating rqd: the build may run twice on the same
    # request object (Skia shadow path + Pillow fallback), and appending the CN name
    # onto team.team_name in place would duplicate it on the second build.
    teams = sorted(rqd.team_info, key=lambda x: x.team_id)
    tids = [team.team_id for team in teams]
    tnames = [f"{team.team_name} ({team.team_cn_name})" if team.team_cn_name else team.team_name for team in teams]
    ticons = await asyncio.gather(*(get_asset_image_ref(ASSETS_BASE_DIR, team.team_icon_path) for team in teams))

    win_tid = tids[0] if teams[0].win_rate >= teams[1].win_rate else tids[1]

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16).set_item_bg(roundrect_bg(alpha=80)):
            with HSplit().set_content_align("rt").set_item_align("rt").set_padding(16).set_sep(7):
                with VSplit().set_content_align("lt").set_item_align("lt").set_sep(5):
                    TextBox(
                        f"【{rqd.region.upper()}-{eid}】{truncate(event_name, 20)}",
                        TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK),
                    )
                    TextBox(
                        f"{event_start.strftime('%Y-%m-%d %H:%M')} ~ {event_end.strftime('%Y-%m-%d %H:%M')}",
                        TextStyle(font=DEFAULT_FONT, size=18, color=BLACK),
                    )
                    time_to_end = event_end - now
                    if time_to_end.total_seconds() <= 0:
                        time_to_end = _EVENT_ENDED_TEXT
                    else:
                        time_to_end = f"距离活动结束还有{get_readable_timedelta(time_to_end)}"
                    TextBox(time_to_end, TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                    TextBox(
                        f"预测更新时间: {rqd.updated_at.strftime('%m-%d %H:%M:%S')} "
                        f"({get_readable_datetime(rqd.updated_at, show_original_time=False)})",
                        TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK),
                    )
                    TextBox("数据来源: 3-3.dev", TextStyle(font=DEFAULT_FONT, size=12, color=(50, 50, 50, 255)))
                if banner_img:
                    ImageBox(banner_img, size=(140, None))

            with (
                VSplit()
                .set_content_align("lt")
                .set_item_align("lt")
                .set_sep(16)
                .set_padding(16)
                .set_item_bg(roundrect_bg(alpha=80))
            ):
                for i in range(2):
                    with HSplit().set_content_align("c").set_item_align("c").set_sep(8).set_padding(16):
                        ImageBox(ticons[i], size=(None, 100))
                        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8):
                            TextBox(
                                tnames[i],
                                TextStyle(font=DEFAULT_BOLD_FONT, size=28, color=BLACK),
                                use_real_line_count=True,
                            ).set_w(400)
                            with HSplit().set_content_align("lb").set_item_align("lb").set_sep(8).set_padding(0):
                                TextBox("预测胜率: ", TextStyle(font=DEFAULT_FONT, size=28, color=(75, 75, 75, 255)))
                                TextBox(
                                    f"{teams[i].win_rate * 100.0:.1f}%",
                                    TextStyle(
                                        font=DEFAULT_BOLD_FONT,
                                        size=32,
                                        color=(25, 100, 25, 255) if win_tid == tids[i] else (100, 25, 25, 255),
                                    ),
                                )
                                TextBox(
                                    "（急募中）" if teams[i].is_recruiting else "",
                                    TextStyle(font=DEFAULT_FONT, size=28, color=(100, 25, 75, 255)),
                                )

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_winrate_predict_image(rqd: WinRateRequest) -> Image.Image:
    return await (await _build_winrate_predict_canvas(rqd)).get_img(2.0)


async def try_render_winrate_predict_payload(rqd: WinRateRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_winrate_predict_canvas(rqd), endpoint="sk_winrate", scale=2.0)
