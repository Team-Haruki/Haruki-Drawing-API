from datetime import datetime, timedelta
from typing import Optional, List, Dict

from PIL import Image
from pydantic import BaseModel

from src.base.configs import ASSETS_BASE_DIR
from src.base.draw import (
    BG_PADDING,
    SEKAI_BLUE_BG,
    add_watermark,
    roundrect_bg,
)
from src.base.painter import (
    BLACK,
    DEFAULT_BOLD_FONT,
    DEFAULT_FONT,
)
from src.base.plot import (
    Canvas,
    FillBg,
    Frame,
    HSplit,
    ImageBox,
    TextBox,
    TextStyle,
    VSplit,
)
from src.base.utils import get_img_from_path, get_readable_datetime, get_readable_timedelta, truncate


class Rank(BaseModel):
    rank: int
    name: str
    score: int | None = None
    time: datetime
    average_round: Optional[int]
    average_pt: Optional[int]
    latest_pt: Optional[int]
    speed: Optional[int]
    min20_times_3_speed: Optional[int]
    hour_round: Optional[int]
    record_startAt: Optional[datetime]

class SklRequest(BaseModel):
    id: int
    region: str
    startAt: int
    aggregateAt: int
    name: str
    banner_img_path: str
    wl_cid: int | None = None
    chara_icon_path: str | None = None
    ranks: list[Rank]

class SKRequest(BaseModel):
    id: int
    region: str
    name: str
    aggregateAt: int
    ranks: list[Rank]
    wl_chara_icon_path: str | None = None
    chara_icon_path: str | None = None
    prev_ranks: Rank | None = None
    next_ranks: Rank | None = None

class CFRequest(BaseModel):
    eid: int
    event_name: str
    region: str
    ranks: List[Rank]
    prev_rank: Rank | None = None
    next_rank: Rank | None = None
    aggregateAt: int
    updateAt: datetime
    wl_chara_icon_path: Optional[str] = None

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
    1500, 2500,
    *range(10000, 50001, 10000),
    *range(100000, 500001, 100000),
]


def get_event_id_and_name_text(region: str, event_id: int, event_name: str) -> str:
    if event_id < 1000:
        return f"【{region.upper()}-{event_id}】{event_name}"
    else:
        chapter_id = event_id // 1000
        event_id = event_id % 1000
        return f"【{region.upper()}-{event_id}-第{chapter_id}章单榜】{event_name}"

# 获取榜线排名字符串
def get_board_rank_str(rank: int) -> str:
    # 每3位加一个逗号
    return f"{rank:,}"

# 获取榜线分数字符串
def get_board_score_str(score: int, width: int = None) -> str:
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
    eid = rqd.id
    event_start = datetime.fromtimestamp(rqd.startAt / 1000)
    event_end = datetime.fromtimestamp(rqd.aggregateAt / 1000 + 1)
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
                    TextBox(get_event_id_and_name_text(region, eid, truncate(title, 16)), TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
                    TextBox(f"{event_start.strftime('%Y-%m-%d %H:%M')} ~ {event_end.strftime('%Y-%m-%d %H:%M')}",
                            TextStyle(font=DEFAULT_FONT, size=18, color=BLACK))
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
                item_style  = TextStyle(font=DEFAULT_FONT,      size=20, color=BLACK)
                with VSplit().set_content_align("c").set_item_align("c").set_sep(8).set_padding(8):
                    with HSplit().set_content_align("c").set_item_align("c").set_sep(5).set_padding(0):
                        TextBox("排名", title_style).set_bg(bg1).set_size((140, gh)).set_content_align("c")
                        # TextBox("名称", title_style).set_bg(bg1).set_size((160, gh)).set_content_align('c')
                        TextBox("分数", title_style).set_bg(bg1).set_size((180, gh)).set_content_align("c")
                        TextBox("RT",  title_style).set_bg(bg1).set_size((180, gh)).set_content_align("c")
                    for i, rank in enumerate(ranks):
                        with HSplit().set_content_align("c").set_item_align("c").set_sep(5).set_padding(0):
                            bg = bg2 if i % 2 == 0 else bg1
                            r = get_board_rank_str(rank.rank)
                            score = get_board_score_str(rank.score)
                            rt = get_readable_datetime(rank.time, show_original_time=False, use_en_unit=False)
                            TextBox(r,          item_style, overflow="clip").set_bg(bg).set_size((140, gh)).set_content_align("r").set_padding((16, 0))
                            # TextBox(rank.name,  item_style,                ).set_bg(bg).set_size((160, gh)).set_content_align('l').set_padding((8,  0))
                            TextBox(score,      item_style, overflow="clip").set_bg(bg).set_size((180, gh)).set_content_align("r").set_padding((16, 0))
                            TextBox(rt,         item_style, overflow="clip").set_bg(bg).set_size((180, gh)).set_content_align("r").set_padding((16, 0))
            else:
                TextBox("暂无榜线数据", TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)).set_padding(32)

    add_watermark(canvas)
    return await canvas.get_img()

# 合成榜线查询图片
async def compose_sk_image(rqd: SKRequest, qtype: str, qval: str | int | list[int], event: dict = None) -> Image.Image:
    eid = rqd.id
    title = rqd.name
    event_end = datetime.fromtimestamp(rqd.aggregateAt / 1000 + 1)
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
            texts.append((f"{prev_rank.rank}名分数: {get_board_score_str(prev_rank.score)}  ↑{get_board_score_str(dlt_score)}", style2))
        if next_rank := rqd.next_ranks:
            dlt_score = rank.score - next_rank.score
            texts.append((f"{next_rank.rank}名分数: {get_board_score_str(next_rank.score)}  ↓{get_board_score_str(dlt_score)}", style2))
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
                    TextBox(get_event_id_and_name_text(rqd.region, eid, truncate(title, 20)), TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
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
async def compose_cf_image(rqd: CFRequest, qtype: str, qval: str | int, event: dict = None) -> Image.Image:
    eid = rqd.eid
    title = rqd.event_name
    event_end = datetime.fromtimestamp(rqd.aggregateAt / 1000 + 1)
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
        texts.append((f"数据开始于: {get_readable_datetime(rank.record_startAt, show_original_time=False)}", style2))
        texts.append((f"数据更新于: {get_readable_datetime(rqd.updateAt, show_original_time=False)}", style2))
    else:
        # 多个
        for rank in ranks:
            texts.append((f"{rqd.event_name}", style1))
            texts.append((f"当前排名 {get_board_rank_str(rank.rank)} - 当前分数 {get_board_score_str(rank.score)}", style2))
            texts.append((f"时速: {get_board_score_str(rank.speed)} - 近{rank.average_round}次平均Pt: {rank.average_pt:.1f}", style2))
            texts.append((f"本小时周回数: {rank.hour_round}", style2))
            texts.append((f"RT: {get_readable_datetime(rank.record_startAt, show_original_time=False)} ~ {get_readable_datetime(rqd.updateAt, show_original_time=False)}", style2))

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(8).set_item_bg(roundrect_bg(alpha=80)):
            with HSplit().set_content_align("rt").set_item_align("rt").set_padding(8).set_sep(7):
                with VSplit().set_content_align("lt").set_item_align("lt").set_sep(5):
                    TextBox(get_event_id_and_name_text(rqd.region, eid, truncate(title, 20)), TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=BLACK))
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
