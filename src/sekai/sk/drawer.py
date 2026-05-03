import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import math
import os

import matplotlib
from matplotlib import font_manager
import matplotlib.dates as mdates
from matplotlib.figure import Figure
import matplotlib.patheffects as patheffects
from matplotlib.ticker import FuncFormatter
from PIL import Image

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
    get_img_from_path,
    get_readable_datetime,
    get_readable_timedelta,
    plt_fig_to_image,
    truncate,
)
from src.settings import ASSETS_BASE_DIR, DEFAULT_THREAD_POOL_SIZE

from .model import (
    CFRequest,
    CSBRequest,
    PlayerTraceRequest,
    RankInfo,
    RankTraceRequest,
    SklRequest,
    SKRequest,
    SpeedInfo,  # noqa: F401 - used in type annotations via Request classes
    SpeedRequest,
    TeamInfo,  # noqa: F401 - used in type annotations via Request classes
    WinRateRequest,
)

matplotlib.use("Agg")
_matplotlib_workers = max(1, min(DEFAULT_THREAD_POOL_SIZE, os.cpu_count() or 1))
_matplotlib_executor = ThreadPoolExecutor(max_workers=_matplotlib_workers, thread_name_prefix="sk-matplotlib")


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

SKL_QUERY_RANKS = [
    *range(10, 51, 10),
    *range(100, 501, 100),
    *range(1000, 5001, 1000),
    *range(10000, 50001, 10000),
    *range(100000, 500001, 100000),
]
ALL_RANKS = [
    *range(1, 100),
    *range(100, 501, 100),
    *range(1000, 5001, 1000),
    1500,
    2500,
    *range(10000, 50001, 10000),
    *range(100000, 500001, 100000),
]

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


async def compose_skl_image(rqd: SklRequest) -> Image.Image:
    """
    合成通过排名列表图片 (SKL)

    Args:
        rqd: 请求数据
    """
    eid = rqd.id
    event_start = datetime_from_millis(rqd.start_at, rqd.timezone)
    event_end = datetime_from_millis(rqd.aggregate_at + 1000, rqd.timezone)
    now = request_now(rqd.timezone)
    title = rqd.name
    banner_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.banner_img_path)
    wl_cid = rqd.wl_cid
    region = rqd.region

    full = rqd.full
    query_ranks = ALL_RANKS if full else SKL_QUERY_RANKS
    query_rank_set = set(query_ranks)
    forecast_columns = list(rqd.forecast_columns or [])
    is_predict_mode = len(forecast_columns) > 0
    prediction_notice = rqd.prediction_notice
    if is_predict_mode and not prediction_notice:
        prediction_notice = "预测数据仅供参考，请以实际为准规划好冲榜计划"
    current_ranks = list(rqd.current_ranks or rqd.ranks)

    if not full:
        current_ranks = [r for r in current_ranks if r.rank in query_rank_set]
        forecast_columns = [
            col.model_copy(update={"ranks": [r for r in col.ranks if r.rank in query_rank_set]})
            for col in forecast_columns
        ]

    current_by_rank = {r.rank: r for r in current_ranks}
    forecast_by_rank = [{r.rank: r for r in col.ranks} for col in forecast_columns]

    rank_set: set[int] = set()
    if is_predict_mode:
        rank_set.update(current_by_rank.keys())
        for col_map in forecast_by_rank:
            rank_set.update(col_map.keys())
    else:
        rank_set.update(r.rank for r in current_ranks)
    ranks = sorted(rank_set)

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_item_bg(roundrect_bg(alpha=80)):
            with HSplit().set_content_align("rt").set_item_align("rt").set_padding(8).set_sep(7):
                with VSplit().set_content_align("lt").set_item_align("lt").set_sep(5):
                    TextBox(
                        get_event_id_and_name_text(region, eid, truncate(title, 16)),
                        TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK),
                    )
                    TextBox(
                        f"{event_start.strftime('%Y-%m-%d %H:%M')} ~ {event_end.strftime('%Y-%m-%d %H:%M')}",
                        TextStyle(font=DEFAULT_FONT, size=18, color=BLACK),
                    )
                    time_to_end = event_end - now
                    if time_to_end.total_seconds() <= 0:
                        time_to_end = "活动已结束"
                    else:
                        time_to_end = f"距离活动结束还有{get_readable_timedelta(time_to_end)}"
                    TextBox(time_to_end, TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                with Frame().set_content_align("r"):
                    if banner_img:
                        ImageBox(banner_img, size=(140, None))
                    if wl_cid:
                        ImageBox(await get_img_from_path(ASSETS_BASE_DIR, rqd.chara_icon_path), size=(None, 50))

            if ranks:
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
                    if is_predict_mode:
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
                            for col in forecast_columns:
                                TextBox(col.name, title_style).set_bg(bg1).set_size((gw, gh)).set_content_align("c")

                            for i, rank in enumerate(ranks):
                                bg = bg2 if i % 2 == 0 else bg1
                                TextBox(get_board_rank_str(rank), item_style, overflow="clip").set_bg(bg).set_size(
                                    (gw, gh)
                                ).set_content_align("c")

                                current = current_by_rank.get(rank)
                                current_score = "-"
                                if current is not None and current.score is not None:
                                    current_score = get_board_score_str(current.score)
                                TextBox(current_score, item_style, overflow="clip").set_bg(bg).set_size(
                                    (gw, gh)
                                ).set_content_align("r").set_padding((16, 0))

                                for idx, col in enumerate(forecast_columns):
                                    source_score = "-"
                                    source_rank = forecast_by_rank[idx].get(rank)
                                    if source_rank is not None and source_rank.score is not None:
                                        source_score = get_board_score_str(source_rank.score)
                                    TextBox(source_score, item_style, overflow="clip").set_bg(bg).set_size(
                                        (gw, gh)
                                    ).set_content_align("r").set_padding((16, 0))

                            footer_bg = bg2 if len(ranks) % 2 == 0 else bg1
                            TextBox("预测时间", title_style, overflow="clip").set_bg(footer_bg).set_size(
                                (gw, gh)
                            ).set_content_align("c")
                            TextBox("-", item_style, overflow="clip").set_bg(footer_bg).set_size(
                                (gw, gh)
                            ).set_content_align("c")
                            for col in forecast_columns:
                                forecast_time_text = "-"
                                if col.forecast_time:
                                    forecast_time_text = get_readable_datetime(
                                        col.forecast_time, show_original_time=False, use_en_unit=False
                                    )
                                TextBox(forecast_time_text, item_style, overflow="clip").set_bg(footer_bg).set_size(
                                    (gw, gh)
                                ).set_content_align("c")

                            update_bg = bg1 if footer_bg == bg2 else bg2
                            TextBox("获取时间", title_style, overflow="clip").set_bg(update_bg).set_size(
                                (gw, gh)
                            ).set_content_align("c")
                            current_update_text = "-"
                            if current_ranks:
                                latest_current = max(current_ranks, key=lambda x: x.time)
                                current_update_text = get_readable_datetime(
                                    latest_current.time, show_original_time=False, use_en_unit=False
                                )
                            TextBox(current_update_text, item_style, overflow="clip").set_bg(update_bg).set_size(
                                (gw, gh)
                            ).set_content_align("c")
                            for col in forecast_columns:
                                update_time_text = "-"
                                if col.update_time:
                                    update_time_text = get_readable_datetime(
                                        col.update_time, show_original_time=False, use_en_unit=False
                                    )
                                TextBox(update_time_text, item_style, overflow="clip").set_bg(update_bg).set_size(
                                    (gw, gh)
                                ).set_content_align("c")
                    else:
                        with HSplit().set_content_align("c").set_item_align("c").set_sep(5).set_padding(0):
                            TextBox("排名", title_style).set_bg(bg1).set_size((140, gh)).set_content_align("c")
                            TextBox("分数", title_style).set_bg(bg1).set_size((180, gh)).set_content_align("c")
                            TextBox("RT", title_style).set_bg(bg1).set_size((180, gh)).set_content_align("c")
                        for i, rank in enumerate(current_ranks):
                            with HSplit().set_content_align("c").set_item_align("c").set_sep(5).set_padding(0):
                                bg = bg2 if i % 2 == 0 else bg1
                                score = get_board_score_str(rank.score)
                                rt = get_readable_datetime(rank.time, show_original_time=False, use_en_unit=False)
                                TextBox(get_board_rank_str(rank.rank), item_style, overflow="clip").set_bg(bg).set_size(
                                    (140, gh)
                                ).set_content_align("r").set_padding((16, 0))
                                TextBox(score, item_style, overflow="clip").set_bg(bg).set_size(
                                    (180, gh)
                                ).set_content_align("r").set_padding((16, 0))
                                TextBox(rt, item_style, overflow="clip").set_bg(bg).set_size(
                                    (180, gh)
                                ).set_content_align("r").set_padding((16, 0))
            else:
                TextBox("暂无榜线数据", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)).set_padding(32)

    add_request_watermark(canvas, rqd)
    return await canvas.get_img()


# 合成榜线查询图片
async def compose_sk_image(rqd: SKRequest) -> Image.Image:
    """
    合成活动排名查询结果图片 (SK/SKK)

    展示特定排名的分数、RT、时速以及前后排名的分差
    """
    eid = rqd.id
    title = rqd.name
    event_end = datetime_from_millis(rqd.aggregate_at + 1000, rqd.timezone)
    now = request_now(rqd.timezone)
    if rqd.wl_chara_icon_path:
        wl_chara_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.wl_chara_icon_path)

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
                        time_to_end = "活动已结束"
                    else:
                        time_to_end = f"距离活动结束还有{get_readable_timedelta(time_to_end)}"
                    TextBox(time_to_end, TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                if rqd.wl_chara_icon_path is not None:
                    ImageBox(wl_chara_img, size=(None, 50))

            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(6).set_padding(16):
                for text, style in texts:
                    TextBox(text, style)

    add_request_watermark(canvas, rqd)
    return await canvas.get_img(1.5)


# 合成查房图片
async def compose_cf_image(rqd: CFRequest) -> Image.Image:
    """
    合成查房结果图片 (CF)

    展示特定玩家的实时排名、分数、时速、周回数等详细数据
    """
    eid = rqd.eid
    title = rqd.event_name
    event_end = datetime_from_millis(rqd.aggregate_at + 1000, rqd.timezone)
    now = request_now(rqd.timezone)
    wl_chara_img_path = rqd.wl_chara_icon_path

    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)
    style2 = TextStyle(font=DEFAULT_FONT, size=24, color=BLACK)
    style3 = TextStyle(font=DEFAULT_FONT, size=20, color=BLACK)
    texts: list[str, TextStyle] = []

    ranks = rqd.ranks
    request_player_name = (rqd.name or rqd.username or "").strip()

    def first_non_empty(*values: str | None) -> str:
        for value in values:
            if value is None:
                continue
            stripped = value.strip()
            if stripped:
                return stripped
        return rqd.event_name

    if len(ranks) == 1:
        # 单个
        rank = ranks[0]
        player_title = first_non_empty(request_player_name, rank.name, rqd.event_name)
        rank_score_text = get_board_score_str(rank.score)
        avg_round_text = str(rank.average_round) if rank.average_round is not None else "?"
        avg_pt_text = f"{rank.average_pt:.1f}" if rank.average_pt is not None else "?"
        latest_pt_text = str(rank.latest_pt) if rank.latest_pt is not None else "?"
        hour_round_text = str(rank.hour_round) if rank.hour_round is not None else "?"
        record_start_text = (
            get_readable_datetime(rank.record_start_at, show_original_time=False)
            if rank.record_start_at is not None
            else "未知"
        )

        texts.append((player_title, style1))
        texts.append((f"当前排名 {rank.rank} - 当前分数 {rank_score_text}", style2))
        if prev_rank := rqd.prev_rank:
            prev_score_text = get_board_score_str(prev_rank.score)
            score_gap = (
                get_board_score_str(prev_rank.score - rank.score)
                if prev_rank.score is not None and rank.score is not None
                else "?"
            )
            texts.append((f"{prev_rank.rank}名分数: {prev_score_text}  ↑{score_gap}", style3))
        if next_rank := rqd.next_rank:
            next_score_text = get_board_score_str(next_rank.score)
            score_gap = (
                get_board_score_str(rank.score - next_rank.score)
                if next_rank.score is not None and rank.score is not None
                else "?"
            )
            texts.append((f"{next_rank.rank}名分数: {next_score_text}  ↓{score_gap}", style3))
        texts.append((f"近{avg_round_text}次平均Pt: {avg_pt_text}", style2))
        texts.append((f"最近一次Pt: {latest_pt_text}", style2))
        texts.append((f"时速: {get_board_score_str(rank.speed)}", style2))
        if rank.min20_times_3_speed is not None:
            texts.append((f"20min×3时速: {get_board_score_str(rank.min20_times_3_speed)}", style2))
        texts.append((f"本小时周回数: {hour_round_text}", style2))
        texts.append((f"数据开始于: {record_start_text}", style2))
        texts.append((f"数据更新于: {get_readable_datetime(rqd.update_at, show_original_time=False)}", style2))
    else:
        # 多个
        for rank in ranks:
            player_title = first_non_empty(rank.name, request_player_name, rqd.event_name)
            avg_round_text = str(rank.average_round) if rank.average_round is not None else "?"
            avg_pt_text = f"{rank.average_pt:.1f}" if rank.average_pt is not None else "?"
            hour_round_text = str(rank.hour_round) if rank.hour_round is not None else "?"
            record_start_text = (
                get_readable_datetime(rank.record_start_at, show_original_time=False)
                if rank.record_start_at is not None
                else "未知"
            )
            texts.append((player_title, style1))
            texts.append(
                (f"当前排名 {get_board_rank_str(rank.rank)} - 当前分数 {get_board_score_str(rank.score)}", style2)
            )
            texts.append(
                (
                    f"时速: {get_board_score_str(rank.speed)} - 近{avg_round_text}次平均Pt: {avg_pt_text}",
                    style2,
                )
            )
            texts.append((f"本小时周回数: {hour_round_text}", style2))
            texts.append(
                (
                    f"RT: {record_start_text} ~ "
                    f"{get_readable_datetime(rqd.update_at, show_original_time=False, use_en_unit=False)}",
                    style2,
                )
            )

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
                        time_to_end = "活动已结束"
                    else:
                        time_to_end = f"距离活动结束还有{get_readable_timedelta(time_to_end)}"
                    TextBox(time_to_end, TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                if wl_chara_img_path:
                    ImageBox(await get_img_from_path(ASSETS_BASE_DIR, wl_chara_img_path), size=(None, 50))

            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(6).set_padding(16):
                for text, style in texts:
                    TextBox(text, style)

    add_request_watermark(canvas, rqd)
    return await canvas.get_img(1.5)


async def compose_csb_image(rqd: CSBRequest) -> Image.Image:
    """
    合成查水表热力图图片 (CSB)

    展示玩家各小时 Pt 变化次数热力图以及停车区间
    """
    eid = rqd.eid
    title = rqd.event_name
    event_end = datetime_from_millis(rqd.aggregate_at + 1000, rqd.timezone)
    now = request_now(rqd.timezone)
    wl_chara_img_path = rqd.wl_chara_icon_path

    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)
    style2 = TextStyle(font=DEFAULT_FONT, size=20, color=BLACK)
    heat_title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=BLACK)
    heat_hint_style = TextStyle(font=DEFAULT_FONT, size=18, color=BLACK)

    ranks = sorted(rqd.ranks, key=lambda item: item.time)
    latest_rank = ranks[-1]
    latest_name = truncate(latest_rank.name, 40)

    rankcounts: list[list[int]] = []
    playcounts: list[list[int]] = []
    start_date = ranks[0].time.date()

    for i in range(len(ranks) - 1):
        cur = ranks[i]
        nxt = ranks[i + 1]
        day = (cur.time.date() - start_date).days
        while len(rankcounts) <= day:
            rankcounts.append([0] * 24)
            playcounts.append([0] * 24)

        hour = cur.time.hour
        rankcounts[day][hour] += 1
        cur_score = cur.score or 0
        next_score = nxt.score or 0
        if next_score > cur_score:
            playcounts[day][hour] += 1

    stop_segments: list[tuple[RankInfo, RankInfo]] = []
    left = None
    right = None
    for rank in ranks:
        if left is None:
            left = rank
        if right is None:
            right = rank

        if rank.rank > 100 or rank.time - right.time > SK_RECORD_TOLERANCE:
            if left != right:
                stop_segments.append((left, right))
            left, right = rank, None
        elif (rank.score or 0) != (right.score or 0):
            if left != right:
                stop_segments.append((left, right))
            left, right = rank, None
        else:
            right = rank
    if left is not None and right is not None:
        stop_segments.append((left, right))

    stop_hours: list[list[bool]] = [[False] * 24 for _ in range(len(rankcounts))]

    def mark_stop_hours(start_time, end_time):
        hour_cursor = start_time.replace(minute=0, second=0, microsecond=0)
        end_hour = end_time.replace(minute=0, second=0, microsecond=0)
        while hour_cursor <= end_hour:
            day = (hour_cursor.date() - start_date).days
            while len(stop_hours) <= day:
                stop_hours.append([False] * 24)
            stop_hours[day][hour_cursor.hour] = True
            hour_cursor += timedelta(hours=1)

    stop_texts: list[tuple[str, TextStyle]] = [(f'T{latest_rank.rank} "{latest_name}" 的停车区间', style1)]
    for left_rank, right_rank in stop_segments:
        if left_rank == right_rank:
            continue
        duration = right_rank.time - left_rank.time
        if duration < SK_CSB_STOP_THRESHOLD:
            continue
        mark_stop_hours(left_rank.time, right_rank.time)
        start_text = left_rank.time.strftime("%m-%d %H:%M")
        end_text = right_rank.time.strftime("%m-%d %H:%M")
        stop_texts.append((f"{start_text} ~ {end_text}（{get_readable_timedelta(duration)}）", style2))
    if len(stop_texts) == 1:
        stop_texts.append(("未找到停车区间", style2))

    row_num = len(stop_texts) // 2 + 1
    first_text = stop_texts[0]
    left_texts = stop_texts[1:row_num]
    right_texts = stop_texts[row_num:]

    heat_color_min = (184, 216, 255)
    heat_color_max = (255, 181, 181)
    heat_color_mysekai = (204, 255, 204)

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
                        time_to_end = "活动已结束"
                    else:
                        time_to_end = f"距离活动结束还有{get_readable_timedelta(time_to_end)}"
                    TextBox(time_to_end, TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                    update_text = get_readable_datetime(
                        rqd.update_at,
                        show_original_time=False,
                        use_en_unit=False,
                    )
                    TextBox(
                        f"数据更新于: {update_text}",
                        TextStyle(font=DEFAULT_FONT, size=16, color=BLACK),
                    )
                if wl_chara_img_path:
                    ImageBox(await get_img_from_path(ASSETS_BASE_DIR, wl_chara_img_path), size=(None, 50))

            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_padding(16):
                TextBox(f'T{latest_rank.rank} "{latest_name}" 各小时Pt变化次数', heat_title_style)
                TextBox("标注*号的小时存在停车区间", heat_hint_style)
                with Grid(col_count=24).set_sep(1, 1):
                    for hour in range(24):
                        TextBox(str(hour), TextStyle(font=DEFAULT_FONT, size=12, color=BLACK)).set_content_align(
                            "c"
                        ).set_size((30, 30))
                    for day in range(len(rankcounts)):
                        for hour in range(24):
                            playcount = playcounts[day][hour]
                            rankcount = rankcounts[day][hour]
                            if rankcount < 10:
                                Spacer(w=30, h=30)
                                continue

                            label = str(playcount)
                            if day < len(stop_hours) and stop_hours[day][hour]:
                                label += "*"
                            if playcount > SK_PLAYCOUNT_MYSEKAI_THRESHOLD:
                                color = heat_color_mysekai
                            else:
                                color = lerp_color(
                                    heat_color_min,
                                    heat_color_max,
                                    max(min((playcount - 15) / 15, 1.0), 0.0),
                                )
                            TextBox(label, TextStyle(font=DEFAULT_FONT, size=14, color=BLACK), overflow="clip").set_bg(
                                roundrect_bg(fill=color, radius=4)
                            ).set_content_align("c").set_size((30, 30)).set_offset((0, -2))

            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(6).set_padding(16):
                TextBox(*first_text)
                with HSplit().set_content_align("lt").set_item_align("lt").set_sep(12):
                    with VSplit().set_content_align("lt").set_item_align("lt").set_sep(4):
                        for text in left_texts:
                            TextBox(*text)
                    with VSplit().set_content_align("lt").set_item_align("lt").set_sep(4):
                        for text in right_texts:
                            TextBox(*text)

    add_request_watermark(canvas, rqd)
    return await canvas.get_img(1.5 if len(stop_texts) < 10 else 1.0)


async def compose_sks_image(rqd: SpeedRequest) -> Image.Image:
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
    banner_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.banner_img_path)
    is_wl_event = rqd.is_wl_event
    query_ranks = SKL_QUERY_RANKS
    period = rqd.period
    ranks = rqd.ranks
    speeds: list[tuple[int, int, int, datetime]] = []
    for rank in ranks:
        if rank.rank in query_ranks:
            speeds.append((rank.rank, rank.score, rank.speed, rank.record_time))
    speeds.sort(key=lambda x: x[0])
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
                        time_to_end = "活动已结束"
                    else:
                        time_to_end = f"距离活动结束还有{get_readable_timedelta(time_to_end)}"
                    TextBox(time_to_end, TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                with Frame().set_content_align("r"):
                    if banner_img:
                        ImageBox(banner_img, size=(140, None))
                    if is_wl_event:
                        ImageBox(await get_img_from_path(ASSETS_BASE_DIR, rqd.wl_chara_icon_path), size=(None, 50))

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
    return await canvas.get_img()


async def compose_player_trace_image(rqd: PlayerTraceRequest) -> Image.Image:
    """
    合成玩家排名追踪图表 (Rating Trace)

    使用 Matplotlib 绘制双轴图表：
    - 左轴: 分数折线图
    - 右轴: 排名散点图
    """
    eid = rqd.event_id
    wl_chara_icon = None
    if rqd.wl_chara_icon_path:
        wl_chara_icon = await get_img_from_path(ASSETS_BASE_DIR, rqd.wl_chara_icon_path)

    ranks = rqd.ranks
    ranks2 = rqd.ranks2

    ranks = [r for r in ranks if r.rank <= 100]
    if not ranks:
        raise ValueError("player trace requires at least one rank entry within top 100")
    if ranks2 is not None:
        ranks2 = [r for r in ranks2 if r.rank <= 100]
        if not ranks2:
            ranks2 = None

    ranks.sort(key=lambda x: x.time)
    name = truncate(ranks[-1].name, 40)
    times = [rank.time for rank in ranks]
    scores = [rank.score for rank in ranks]
    rs = [rank.rank for rank in ranks]
    if ranks2 is not None:
        ranks2.sort(key=lambda x: x.time)
        name2 = truncate(ranks2[-1].name, 40)
        times2 = [rank.time for rank in ranks2]
        scores2 = [rank.score for rank in ranks2]
        rs2 = [rank.rank for rank in ranks2]

    def _render_player_trace_plot() -> Image.Image:
        fig = Figure(figsize=(12, 8))
        ax = fig.add_subplot(111)
        try:
            fig.subplots_adjust(wspace=0, hspace=0)

            draw_day_night_bg(ax, times[0], times[-1])

            min_score = min(scores)
            max_score = max(scores)
            if ranks2 is not None:
                min_score = min(min_score, min(scores2))
                max_score = max(max_score, max(scores2))

            lines = []

            color_p1 = ("royalblue", "cornflowerblue")
            color_p2 = ("orangered", "coral")

            # 绘制分数
            (line_score,) = ax.plot(
                times,
                scores,
                "o",
                label=f"{name}分数",
                color=color_p1[0],
                markersize=1,
                linewidth=0.5,
            )
            lines.append(line_score)
            ax.annotate(
                f"{get_board_score_str(scores[-1])}",
                xy=(times[-1], scores[-1]),
                xytext=(times[-1], scores[-1]),
                color=color_p1[0],
                fontsize=12,
                ha="right",
                path_effects=PLOT_LABEL_PATH_EFFECTS,
            )
            if ranks2 is not None:
                (line_score2,) = ax.plot(
                    times2, scores2, "o", label=f"{name2}分数", color=color_p2[0], markersize=1, linewidth=0.5
                )
                lines.append(line_score2)
                ax.annotate(
                    f"{get_board_score_str(scores2[-1])}",
                    xy=(times2[-1], scores2[-1]),
                    xytext=(times2[-1], scores2[-1]),
                    color=color_p2[0],
                    fontsize=12,
                    ha="right",
                    path_effects=PLOT_LABEL_PATH_EFFECTS,
                )

            ax.set_ylim(min_score * 0.95, max_score * 1.05)
            ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: get_board_score_str(x)))
            ax.grid(True, linestyle="-", alpha=0.3, color="gray")
            # 绘制排名
            ax2 = ax.twinx()

            (line_rank,) = ax2.plot(
                times,
                rs,
                "o",
                label=f"{name}排名",
                color=color_p1[1],
                markersize=0.7,
                linewidth=0.5,
            )
            lines.append(line_rank)
            ax2.annotate(
                f"{int(rs[-1])}",
                xy=(times[-1], rs[-1] * 1.02),
                xytext=(times[-1], rs[-1] * 1.02),
                color=color_p1[1],
                fontsize=12,
                ha="right",
                path_effects=PLOT_LABEL_PATH_EFFECTS,
            )
            if ranks2 is not None:
                (line_rank2,) = ax2.plot(
                    times2, rs2, "o", label=f"{name2}排名", color=color_p2[1], markersize=0.7, linewidth=0.5
                )
                lines.append(line_rank2)
                ax2.annotate(
                    f"{int(rs2[-1])}",
                    xy=(times2[-1], rs2[-1] * 1.02),
                    xytext=(times2[-1], rs2[-1] * 1.02),
                    color=color_p2[1],
                    fontsize=12,
                    ha="right",
                    path_effects=PLOT_LABEL_PATH_EFFECTS,
                )

            ax2.yaxis.set_major_formatter(FuncFormatter(lambda x, _: str(int(x)) if 1 <= int(x) <= 100 else ""))
            ax2.set_ylim(110, -10)

            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M", tz=times[0].tzinfo))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            fig.autofmt_xdate()

            if ranks2 is None:
                ax.set_title(f"{get_event_id_and_name_text(rqd.region, eid, '')} 玩家: {name}")
            else:
                ax.set_title(f"{get_event_id_and_name_text(rqd.region, eid, '')} 玩家: {name} vs {name2}")

            labels = [line.get_label() for line in lines]
            legend = ax2.legend(lines, labels, loc="upper left")
            legend.set_zorder(1000)

            return plt_fig_to_image(fig)
        finally:
            fig.clear()

    img = await run_matplotlib_plot(_render_player_trace_plot)
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        ImageBox(img).set_bg(roundrect_bg(fill=(255, 255, 255, 200)))
        if wl_chara_icon is not None:
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
    add_request_watermark(canvas, rqd)
    return await canvas.get_img()


# 合成排名追踪图片
async def compose_rank_trace_image(rqd: RankTraceRequest) -> Image.Image:
    """
    合成排名档位追踪与预测图表

    分析特定档位的分数增长趋势，并根据预测分绘制参考线
    """
    eid = rqd.event_id
    ranks = rqd.ranks
    if not ranks:
        raise ValueError("ranks must not be empty")
    ranks.sort(key=lambda x: x.time)
    times = [rank.time for rank in ranks]
    scores = [rank.score for rank in ranks]
    pred_scores = []
    original_names = [rank.name for rank in ranks]
    unique_names = list(dict.fromkeys(original_names))
    wl_chara_icon = None
    if rqd.wl_chara_icon_path:
        wl_chara_icon = await get_img_from_path(ASSETS_BASE_DIR, rqd.wl_chara_icon_path)

    # 时速计算
    speeds = []
    min_period = timedelta(minutes=50)
    max_period = timedelta(minutes=60)
    left = 0
    for right in range(0, len(ranks)):
        while ranks[right].time - ranks[left].time > max_period:
            left += 1
        if min_period <= ranks[right].time - ranks[left].time <= max_period:
            speed = (
                (ranks[right].score - ranks[left].score) / (ranks[right].time - ranks[left].time).total_seconds() * 3600
            )
            speeds.append(speed)
        else:
            speeds.append(-1)

    # 附加排名预测
    final_score = rqd.predict_ranks.score if rqd.predict_ranks is not None else None

    max_score = max(scores + pred_scores)
    min_score = min(scores + pred_scores)
    if final_score is not None:
        max_score = max(max_score, final_score)
        min_score = min(min_score, final_score)

    def _render_rank_trace_plot() -> Image.Image:
        fig = Figure(figsize=(12, 8))
        ax = fig.add_subplot(111)
        try:
            fig.subplots_adjust(wspace=0, hspace=0)

            draw_day_night_bg(ax, times[0], times[-1])

            num_unique_names = len(unique_names)
            if num_unique_names > len(RANK_TRACE_SCORE_COLORS):
                # 数量太多，直接使用同一个颜色
                point_colors = [RANK_TRACE_SCORE_COLORS[0] for _ in ranks]
            else:  # 否则为每个玩家分配不同颜色
                name_to_color = {name: RANK_TRACE_SCORE_COLORS[idx] for idx, name in enumerate(unique_names)}

                # 根据原始的、带重复的 name 列表来生成颜色列表
                point_colors = [name_to_color.get(name) for name in original_names]

            # 绘制分数，为不同uid的数据点使用不同颜色
            score_points = ax.scatter(times, scores, c=point_colors, s=3, label="分数线", zorder=3)
            if scores:
                ax.annotate(
                    f"{get_board_score_str(scores[-1])}",
                    xy=(times[-1], scores[-1]),
                    xytext=(times[-1], scores[-1]),
                    color=point_colors[-1],
                    fontsize=12,
                    ha="right",
                    path_effects=PLOT_LABEL_PATH_EFFECTS,
                )

            # 绘制预测线
            if final_score is not None:
                ax.axhline(y=final_score, color="red", linestyle="--", linewidth=0.5)
                ax.text(
                    times[-1],
                    final_score * 1.02,
                    f"预测最终: {get_board_score_str(final_score)}",
                    color="red",
                    fontsize=12,
                    ha="right",
                )

            # 绘制时速
            ax2 = ax.twinx()
            (line_speeds,) = ax2.plot(times, speeds, "o", label="时速", color="green", markersize=0.5, linewidth=0.5)
            ax2.yaxis.set_major_formatter(FuncFormatter(lambda x, _: get_board_score_str(int(x)) + "/h"))
            valid_speeds = [speed for speed in speeds if speed >= 0]
            max_speed = max(valid_speeds) if valid_speeds else 1
            ax2.set_ylim(0, max_speed * 1.2)

            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M", tz=times[0].tzinfo))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            fig.autofmt_xdate()
            ax.set_title(f"{get_event_id_and_name_text(rqd.region, eid, '')} T{rqd.target_rank} 分数线")

            lines = [score_points, line_speeds]
            labels = [line.get_label() for line in lines]
            legend = ax2.legend(lines, labels, loc="upper left")
            legend.set_zorder(1000)

            return plt_fig_to_image(fig)
        finally:
            fig.clear()

    img = await run_matplotlib_plot(_render_rank_trace_plot)
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        ImageBox(img).set_bg(roundrect_bg(fill=(255, 255, 255, 200)))
        if rqd.wl_chara_icon_path is not None:
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
    add_request_watermark(canvas, rqd)
    return await canvas.get_img()


async def compose_winrate_predict_image(rqd: WinRateRequest) -> Image.Image:
    """
    合成团队战胜率预测图片

    展示红白两队的预测胜率、是否急募等信息
    """
    eid = rqd.event_id
    banner_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.banner_img_path)

    event_name = rqd.event_name
    event_start = datetime_from_millis(rqd.event_start_at, rqd.timezone)
    event_end = datetime_from_millis(rqd.event_aggregate_at + 1000, rqd.timezone)
    now = request_now(rqd.timezone)

    teams = rqd.team_info
    teams.sort(key=lambda x: x.team_id)
    tids = [team.team_id for team in teams]
    for team in teams:
        if team.team_cn_name:
            team.team_name = f"{team.team_name} ({team.team_cn_name})"
    tnames = [team.team_name for team in teams]
    ticons = [await get_img_from_path(ASSETS_BASE_DIR, team.team_icon_path) for team in teams]

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
                        time_to_end = "活动已结束"
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
    return await canvas.get_img(2.0)
