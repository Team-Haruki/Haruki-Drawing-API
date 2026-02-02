from datetime import datetime, timedelta
import math

import matplotlib
from matplotlib import font_manager
from matplotlib import pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from PIL import Image
from pydantic import BaseModel

from src.sekai.base.draw import (
    BG_PADDING,
    SEKAI_BLUE_BG,
    add_watermark,
    roundrect_bg,
)
from src.sekai.base.painter import BLACK, DEFAULT_BOLD_FONT, DEFAULT_FONT, lerp_color, rgb_to_color_code
from src.sekai.base.plot import (
    Canvas,
    FillBg,
    Frame,
    HSplit,
    ImageBox,
    TextBox,
    TextStyle,
    VSplit,
)
from src.sekai.base.utils import (
    get_img_from_path,
    get_readable_datetime,
    get_readable_timedelta,
    plt_fig_to_image,
    truncate,
)
from src.settings import ASSETS_BASE_DIR

matplotlib.use("Agg")


class RankInfo(BaseModel):
    r"""RankInfo

    单个排名数据点信息

    Attributes
    ----------
    rank : int
        排名
    name : str
        玩家名称
    score : Optional[int]
        分数
    time : datetime
        记录时间
    average_round : Optional[int]
        平均周回数
    average_pt : Optional[int]
        平均Pt
    latest_pt : Optional[int]
        最新Pt
    speed : Optional[int]
        时速
    min20_times_3_speed : Optional[int]
        20分钟x3时速
    hour_round : Optional[int]
        本小时周回数
    record_start_at : Optional[datetime]
        记录开始时间
    """

    rank: int
    name: str
    score: int | None = None
    time: datetime
    average_round: int | None = None
    average_pt: int | None = None
    latest_pt: int | None = None
    speed: int | None = None
    min20_times_3_speed: int | None = None
    hour_round: int | None = None
    record_start_at: datetime | None = None


class SpeedInfo(BaseModel):
    r"""SpeedInfo

    时速数据点信息

    Attributes
    ----------
    rank : int
        排名
    score : int
        分数
    speed : Optional[int]
        时速
    record_time : datetime
        记录时间
    """

    rank: int
    score: int
    speed: int | None = None
    record_time: datetime


class SklRequest(BaseModel):
    r"""SklRequest

    绘制活动排名列表图片所必需的数据

    Attributes
    ----------
    id : int
        活动ID
    region : str
        服务器区域
    start_at : int
        活动开始时间戳
    aggregate_at : int
        活动结束时间戳
    name : str
        活动名称
    banner_img_path : str
        活动Banner路径
    wl_cid : Optional[int]
        World Link角色ID
    chara_icon_path : Optional[str]
        角色图标路径
    ranks : list[RankInfo]
        排名列表
    """

    id: int
    region: str
    start_at: int
    aggregate_at: int
    name: str
    banner_img_path: str
    wl_cid: int | None = None
    chara_icon_path: str | None = None
    ranks: list[RankInfo]


class SKRequest(BaseModel):
    r"""SKRequest

    绘制排名查询结果图片所必需的数据

    Attributes
    ----------
    id : int
        活动ID
    region : str
        服务器区域
    name : str
        活动名称
    aggregate_at : int
        活动结束时间戳
    ranks : list[RankInfo]
        排名数据列表
    wl_chara_icon_path : Optional[str]
        World Link角色图标路径
    chara_icon_path : Optional[str]
        角色图标路径
    prev_ranks : Optional[RankInfo]
        上一名排名数据
    next_ranks : Optional[RankInfo]
        下一名排名数据
    """

    id: int
    region: str
    name: str
    aggregate_at: int
    ranks: list[RankInfo]
    wl_chara_icon_path: str | None = None
    chara_icon_path: str | None = None
    prev_ranks: RankInfo | None = None
    next_ranks: RankInfo | None = None


class CFRequest(BaseModel):
    r"""CFRequest

    绘制查房结果图片所必需的数据

    Attributes
    ----------
    eid : int
        活动ID
    event_name : str
        活动名称
    region : str
        服务器区域
    ranks : list[RankInfo]
        排名数据列表
    prev_rank : RankInfo | None
        上一名排名数据
    next_rank : RankInfo | None
        下一名排名数据
    aggregate_at : int
        活动结束时间戳
    update_at : datetime
        数据更新时间
    wl_chara_icon_path : str | None
        World Link角色图标路径
    """

    eid: int
    event_name: str
    region: str
    ranks: list[RankInfo]
    prev_rank: RankInfo | None = None
    next_rank: RankInfo | None = None
    aggregate_at: int
    update_at: datetime
    wl_chara_icon_path: str | None = None


class SpeedRequest(BaseModel):
    r"""SpeedRequest

    绘制时速分析图片所必需的数据

    Attributes
    ----------
    event_id : int
        活动ID
    region : str
        服务器区域
    event_name : str
        活动名称
    event_start_at : int
        活动开始时间戳
    event_aggregate_at : int
        活动结束时间戳
    ranks : list[SpeedInfo]
        时速数据列表
    is_wl_event : bool
        是否是World Link活动
    request_type : str
        请求类型说明
    period : timedelta
        时速统计周期
    banner_img_path : str | None
        活动Banner路径
    wl_chara_icon_path : str | None
        World Link角色图标路径
    """

    event_id: int
    region: str
    event_name: str
    event_start_at: int
    event_aggregate_at: int
    ranks: list[SpeedInfo]
    is_wl_event: bool
    request_type: str
    period: timedelta
    banner_img_path: str | None = None
    wl_chara_icon_path: str | None = None


class PlayerTraceRequest(BaseModel):
    r"""PlayerTraceRequest

    绘制玩家排名追踪图片所必需的数据

    Attributes
    ----------
    event_id : int
        活动ID
    region : str
        服务器区域
    wl_chara_icon_path : str | None
        World Link角色图标路径
    ranks : list[RankInfo]
        排名历史数据
    ranks2 : list[RankInfo] | None
        对比玩家的排名历史数据（可选）
    """

    event_id: int
    region: str
    wl_chara_icon_path: str | None = None
    ranks: list[RankInfo]
    ranks2: list[RankInfo] | None = None


class RankTraceRequest(BaseModel):
    r"""RankTraceRequest

    绘制排名档位追踪图片所必需的数据

    Attributes
    ----------
    event_id : int
        活动ID
    region : str
        服务器区域
    wl_chara_icon_path : str | None
        World Link角色图标路径
    target_rank : int
        目标排名
    ranks : list[RankInfo]
        排名历史数据
    predict_ranks : RankInfo | None
        预测排名数据
    """

    event_id: int
    region: str
    wl_chara_icon_path: str | None = None
    target_rank: int
    ranks: list[RankInfo]
    predict_ranks: RankInfo | None = None


class TeamInfo(BaseModel):
    r"""TeamInfo

    团队战队伍信息

    Attributes
    ----------
    team_id : int
        队伍ID
    team_name : str
        队伍名称
    win_rate : float
        队伍胜率 (0.0 - 1.0)
    is_recruiting : bool
        是否急募中
    team_cn_name : Optional[str]
        队伍中文名称
    team_icon_path : Optional[str]
        队伍图标路径
    """

    team_id: int
    team_name: str
    win_rate: float
    is_recruiting: bool
    team_cn_name: str | None = None
    team_icon_path: str | None = None


class WinRateRequest(BaseModel):
    r"""WinRateRequest

    绘制胜率预测图片所必需的数据

    Attributes
    ----------
    event_id : int
        活动ID
    event_name : str
        活动名称
    region : str
        服务器区域
    wl_chara_icon_path : Optional[str]
        World Link角色图标路径
    updated_at : datetime
        预测更新时间
    event_start_at : int
        活动开始时间戳
    event_aggregate_at : int
        活动结束时间戳
    banner_img_path : Optional[str]
        活动Banner路径
    team_info : List[TeamInfo]
        队伍信息列表
    """

    wl_chara_icon_path: str | None = None
    updated_at: datetime
    event_start_at: int
    event_aggregate_at: int
    banner_img_path: str | None = None
    team_info: list[TeamInfo]


# matplotlib字体
font_paths = []
font_paths.append(ASSETS_BASE_DIR / (DEFAULT_FONT + ".otf"))
font_paths.append(ASSETS_BASE_DIR / (DEFAULT_FONT + ".ttf"))
for path in font_paths:
    try:
        font_manager.fontManager.addfont(path)
        prop = font_manager.FontProperties(fname=path)
        font_name = prop.get_name()
        plt.rcParams["font.family"] = [font_name]
        plt.rcParams["axes.unicode_minus"] = False
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
        end = bg_times[i] + interval
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


async def compose_skl_image(rqd: SklRequest, full: bool = False) -> Image.Image:
    """
    合成通过排名列表图片 (SKL)

    Args:
        rqd: 请求数据
        full: 是否显示完整榜线 (True: ALL_RANKS, False: SKL_QUERY_RANKS)
    """
    eid = rqd.id
    event_start = datetime.fromtimestamp(rqd.start_at / 1000)
    event_end = datetime.fromtimestamp(rqd.aggregate_at / 1000 + 1)
    title = rqd.name
    banner_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.banner_img_path)
    wl_cid = rqd.wl_cid
    region = rqd.region

    query_ranks = ALL_RANKS if full else SKL_QUERY_RANKS
    ranks = rqd.ranks if full else [r for r in rqd.ranks if r.rank in query_ranks]
    ranks = sorted(ranks, key=lambda x: x.rank)

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
                    time_to_end = event_end - datetime.now()
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
                    with HSplit().set_content_align("c").set_item_align("c").set_sep(5).set_padding(0):
                        TextBox("排名", title_style).set_bg(bg1).set_size((140, gh)).set_content_align("c")
                        # TextBox("名称", title_style).set_bg(bg1).set_size((160, gh)).set_content_align('c')
                        TextBox("分数", title_style).set_bg(bg1).set_size((180, gh)).set_content_align("c")
                        TextBox("RT", title_style).set_bg(bg1).set_size((180, gh)).set_content_align("c")
                    for i, rank in enumerate(ranks):
                        with HSplit().set_content_align("c").set_item_align("c").set_sep(5).set_padding(0):
                            bg = bg2 if i % 2 == 0 else bg1
                            r = get_board_rank_str(rank.rank)
                            score = get_board_score_str(rank.score)
                            rt = get_readable_datetime(rank.time, show_original_time=False, use_en_unit=False)
                            TextBox(r, item_style, overflow="clip").set_bg(bg).set_size((140, gh)).set_content_align(
                                "r"
                            ).set_padding((16, 0))
                            TextBox(score, item_style, overflow="clip").set_bg(bg).set_size(
                                (180, gh)
                            ).set_content_align("r").set_padding((16, 0))
                            TextBox(rt, item_style, overflow="clip").set_bg(bg).set_size((180, gh)).set_content_align(
                                "r"
                            ).set_padding((16, 0))
            else:
                TextBox("暂无榜线数据", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)).set_padding(32)

    add_watermark(canvas)
    return await canvas.get_img()


# 合成榜线查询图片
async def compose_sk_image(rqd: SKRequest) -> Image.Image:
    """
    合成活动排名查询结果图片 (SK/SKK)

    展示特定排名的分数、RT、时速以及前后排名的分差
    """
    eid = rqd.id
    title = rqd.name
    event_end = datetime.fromtimestamp(rqd.aggregate_at / 1000 + 1)
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
                    time_to_end = event_end - datetime.now()
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

    add_watermark(canvas)
    return await canvas.get_img(1.5)


# 合成查房图片
async def compose_cf_image(rqd: CFRequest) -> Image.Image:
    """
    合成查房结果图片 (CF)

    展示特定玩家的实时排名、分数、时速、周回数等详细数据
    """
    eid = rqd.eid
    title = rqd.event_name
    event_end = datetime.fromtimestamp(rqd.aggregate_at / 1000 + 1)
    wl_chara_img_path = rqd.wl_chara_icon_path

    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)
    style2 = TextStyle(font=DEFAULT_FONT, size=24, color=BLACK)
    style3 = TextStyle(font=DEFAULT_FONT, size=20, color=BLACK)
    texts: list[str, TextStyle] = []

    ranks = rqd.ranks

    if len(ranks) == 1:
        # 单个
        rank = ranks[0]
        texts.append((f"{title}", style1))
        texts.append((f"当前排名 {rank.rank} - 当前分数 {rank.score}", style2))
        if prev_rank := rqd.prev_rank:
            texts.append((f"{prev_rank.rank}名分数: {prev_rank.score}  ↑{prev_rank.score - rank.score}", style3))
        if next_rank := rqd.next_rank:
            texts.append((f"{next_rank.rank}名分数: {next_rank.score}  ↓{next_rank.score - rank.score}", style3))
        texts.append((f"近{rank.average_round}次平均Pt: {rank.average_pt:.1f}", style2))
        texts.append((f"最近一次Pt: {rank.latest_pt}", style2))
        texts.append((f"时速: {get_board_score_str(rank.speed)}", style2))
        if rank.min20_times_3_speed:
            texts.append((f"20min×3时速: {get_board_score_str(rank.min20_times_3_speed)}", style2))
        texts.append((f"本小时周回数: {rank.hour_round}", style2))
        texts.append((f"数据开始于: {get_readable_datetime(rank.record_start_at, show_original_time=False)}", style2))
        texts.append((f"数据更新于: {get_readable_datetime(rqd.update_at, show_original_time=False)}", style2))
    else:
        # 多个
        for rank in ranks:
            texts.append((f"{rqd.event_name}", style1))
            texts.append(
                (f"当前排名 {get_board_rank_str(rank.rank)} - 当前分数 {get_board_score_str(rank.score)}", style2)
            )
            texts.append(
                (
                    f"时速: {get_board_score_str(rank.speed)} - 近{rank.average_round}次平均Pt: {rank.average_pt:.1f}",
                    style2,
                )
            )
            texts.append((f"本小时周回数: {rank.hour_round}", style2))
            texts.append(
                (
                    f"RT: {get_readable_datetime(rank.record_start_at, show_original_time=False)} ~ "
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
                    time_to_end = event_end - datetime.now()
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

    add_watermark(canvas)
    return await canvas.get_img(1.5)


async def compose_sks_image(rqd: SpeedRequest) -> Image.Image:
    """
    合成时速分析图片 (SKS)

    展示各档位的实时时速排名
    """
    unit_text = rqd.request_type
    eid = rqd.event_id
    title = rqd.event_name
    event_start = datetime.fromtimestamp(rqd.event_start_at / 1000)
    event_end = datetime.fromtimestamp(rqd.event_aggregate_at / 1000 + 1)
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
                    time_to_end = event_end - datetime.now()
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

    add_watermark(canvas)
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
    if ranks2 is not None:
        ranks2 = [r for r in ranks2 if r.rank <= 100]

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
    fig, ax = plt.subplots()
    fig.set_size_inches(12, 8)
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
    (line_score,) = ax.plot(times, scores, "o", label=f"{name}分数", color=color_p1[0], markersize=1, linewidth=0.5)
    lines.append(line_score)
    plt.annotate(
        f"{get_board_score_str(scores[-1])}",
        xy=(times[-1], scores[-1]),
        xytext=(times[-1], scores[-1]),
        color=color_p1[0],
        fontsize=12,
        ha="right",
    )
    if ranks2 is not None:
        (line_score2,) = ax.plot(
            times2, scores2, "o", label=f"{name2}分数", color=color_p2[0], markersize=1, linewidth=0.5
        )
        lines.append(line_score2)
        plt.annotate(
            f"{get_board_score_str(scores2[-1])}",
            xy=(times2[-1], scores2[-1]),
            xytext=(times2[-1], scores2[-1]),
            color=color_p2[0],
            fontsize=12,
            ha="right",
        )

    ax.set_ylim(min_score * 0.95, max_score * 1.05)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: get_board_score_str(x)))
    ax.grid(True, linestyle="-", alpha=0.3, color="gray")
    # 绘制排名
    ax2 = ax.twinx()

    (line_rank,) = ax2.plot(times, rs, "o", label=f"{name}排名", color=color_p1[1], markersize=0.7, linewidth=0.5)
    lines.append(line_rank)
    plt.annotate(
        f"{int(rs[-1])}",
        xy=(times[-1], rs[-1] * 1.02),
        xytext=(times[-1], rs[-1] * 1.02),
        color=color_p1[1],
        fontsize=12,
        ha="right",
    )
    if ranks2 is not None:
        (line_rank2,) = ax2.plot(
            times2, rs2, "o", label=f"{name2}排名", color=color_p2[1], markersize=0.7, linewidth=0.5
        )
        lines.append(line_rank2)
        plt.annotate(
            f"{int(rs2[-1])}",
            xy=(times2[-1], rs2[-1] * 1.02),
            xytext=(times2[-1], rs2[-1] * 1.02),
            color=color_p2[1],
            fontsize=12,
            ha="right",
        )

    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: str(int(x)) if 1 <= int(x) <= 100 else ""))
    ax2.set_ylim(110, -10)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate()

    if ranks2 is None:
        plt.title(f"{get_event_id_and_name_text(rqd.region, eid, '')} 玩家: {name}")
    else:
        plt.title(f"{get_event_id_and_name_text(rqd.region, eid, '')} 玩家: {name} vs {name2}")

    labels = [line.get_label() for line in lines]
    ax.legend(lines, labels, loc="upper left")

    img = plt_fig_to_image(fig)
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
    add_watermark(canvas)
    return await canvas.get_img()


# 合成排名追踪图片
async def compose_rank_trace_image(rqd: RankTraceRequest) -> Image.Image:
    """
    合成排名档位追踪与预测图表

    分析特定档位的分数增长趋势，并根据预测分绘制参考线
    """
    eid = rqd.event_id
    ranks = rqd.ranks
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
    final_score = rqd.predict_ranks.score

    max_score = max(scores + pred_scores)
    min_score = min(scores + pred_scores)
    if final_score:
        max_score = max(max_score, final_score)
        min_score = min(min_score, final_score)

    fig, ax = plt.subplots()
    fig.set_size_inches(12, 8)
    fig.subplots_adjust(wspace=0, hspace=0)

    draw_day_night_bg(ax, times[0], times[-1])

    num_unique_names = len(unique_names)
    if num_unique_names > 10:
        # 数量太多，直接使用同一个颜色
        point_colors = ["blue" for _ in ranks]
    else:  # 否则为每个玩家分配不同颜色
        num_part1 = num_unique_names // 2
        num_part2 = num_unique_names - num_part1

        cmap = plt.get_cmap("coolwarm")
        colors1 = cmap(np.linspace(start=0.0, stop=0.4, num=num_part1))
        colors2 = cmap(np.linspace(start=0.6, stop=1.0, num=num_part2))

        if num_unique_names > 0:
            combined_colors = np.vstack((colors1, colors2))
            np.random.shuffle(combined_colors)
        else:
            combined_colors = []

        # 创建从 name 到 color 的映射字典
        name_to_color = dict(zip(unique_names, combined_colors))

        # 根据原始的、带重复的 name 列表来生成颜色列表
        point_colors = [name_to_color.get(name) for name in original_names]

    # 绘制分数，为不同uid的数据点使用不同颜色
    ax.scatter(times, scores, c=point_colors, s=2)
    if scores:
        plt.annotate(
            f"{get_board_score_str(scores[-1])}",
            xy=(times[-1], scores[-1]),
            xytext=(times[-1], scores[-1]),
            color=point_colors[-1],
            fontsize=12,
            ha="right",
        )

    # 绘制预测线
    if final_score:
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
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: get_board_score_str(int(x)) + "/h"))
    ax2.set_ylim(0, max(speeds) * 1.2)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate()
    plt.title(f"{get_event_id_and_name_text(rqd.region, eid, '')} T{rqd.target_rank} 分数线")

    lines = [line_speeds]
    labels = [line.get_label() for line in lines]
    ax.legend(lines, labels, loc="upper left")

    img = plt_fig_to_image(fig)
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        ImageBox(img).set_bg(roundrect_bg(fill=(255, 255, 255, 200)))
        if rqd.wl_chara_icon_path is not None:
            with VSplit().set_content_align("c").set_item_align("c").set_sep(4).set_bg(roundrect_bg()).set_padding(8):
                ImageBox(wl_chara_icon, size=(None, 50))
                TextBox("单榜", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK))
    add_watermark(canvas)
    return await canvas.get_img()


async def compose_winrate_predict_image(rqd: WinRateRequest) -> Image.Image:
    """
    合成团队战胜率预测图片

    展示红白两队的预测胜率、是否急募等信息
    """
    eid = rqd.event_id
    banner_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.banner_img_path)

    event_name = rqd.event_name
    event_start = datetime.fromtimestamp(rqd.event_start_at / 1000)
    event_end = datetime.fromtimestamp(rqd.event_aggregate_at / 1000 + 1)

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
                    time_to_end = event_end - datetime.now()
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

    add_watermark(canvas)
    return await canvas.get_img(2.0)
