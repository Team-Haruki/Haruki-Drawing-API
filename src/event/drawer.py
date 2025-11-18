from datetime import datetime
from typing import Any, List, Optional
import math
from PIL import Image
from pydantic import BaseModel

from src.base.configs import ASSETS_BASE_DIR
from src.base.draw import (
    BG_PADDING,
    CHARACTER_COLOR_CODE,
    SEKAI_BLUE_BG,
    add_watermark,
    roundrect_bg,
    WIDGET_BG_COLOR,
)
from src.base.painter import DEFAULT_BOLD_FONT, DEFAULT_FONT, color_code_to_rgb, DEFAULT_HEAVY_FONT
from src.base.plot import (
    Canvas,
    Frame,
    Grid,
    HSplit,
    ImageBg,
    ImageBox,
    RoundRectBg,
    Spacer,
    TextBox,
    TextStyle,
    VSplit,
)
from src.base.utils import get_img_from_path, get_readable_timedelta
from src.profile.drawer import (
    CardFullThumbnailRequest,
    get_card_full_thumbnail,
    get_detailed_profile_card,
    DetailedProfileCardRequest
)

class EventInfo(BaseModel):
    eid: str
    event_type: str
    start_time: Any
    end_time: Any
    is_wl_event: bool
    banner_cid: int
    banner_index: int
    bonus_attr: str
    bonus_chara_id: Optional[List[int]] = None
    wl_time_list: Optional[list[dict[str, Any]]] = None

class EventHistoryInfo(BaseModel):
    event_id: str
    event_name: str
    event_start_at: int
    event_end_at: int
    rank: Optional[int] = None
    event_point: int
    is_wl_event: bool = False
    banner_path: str
    wl_chara_icon_path: Optional[str] = None

class EventAssets(BaseModel):
    event_bg_path: str
    event_logo_path: str
    event_story_bg_path: str
    event_attr_image_path: str
    event_ban_chara_img: str
    ban_chara_icon_path: str
    bonus_chara_path: Optional[List[str]] = None

class EventDetailRequest(BaseModel):
    region: str
    event_info: EventInfo
    event_assets: EventAssets
    event_cards: list[CardFullThumbnailRequest]

class EventRecordRequest(BaseModel):
    event_info: List[EventHistoryInfo]
    wl_event_info: List[EventHistoryInfo]
    user_info: DetailedProfileCardRequest

class EventBriefInfo(BaseModel):
    event_id: int
    event_name: str
    event_type: str
    event_start_at: int
    event_end_at: int
    event_banner_path: str
    event_cards: Optional[List[CardFullThumbnailRequest]]
    event_attr_path: Optional[str] = None
    event_chara_path: Optional[str] = None
    event_unit_path: Optional[str] = None

class EventListRequest(BaseModel):
    event_info: List[EventBriefInfo]

async def compose_event_detail_image(rqd: EventDetailRequest) -> Image.Image:
    detail = rqd.event_info
    now = datetime.now()
    card_thumbs = []
    for card in rqd.event_cards:
        card_full_thumb = await get_card_full_thumbnail(card)
        card_thumbs.append(card_full_thumb)

    if detail:
        banner_index = rqd.event_info.banner_index
    detail.start_time = datetime.fromtimestamp(rqd.event_info.start_time / 1000)
    detail.end_time = datetime.fromtimestamp(rqd.event_info.end_time / 1000 + 1)

    label_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    text_style = TextStyle(font=DEFAULT_FONT, size=24, color=(70, 70, 70))

    wl_chapters = rqd.event_info.wl_time_list
    if rqd.event_info.is_wl_event:
        for chapter in wl_chapters:
            chapter["start_time"] = datetime.fromtimestamp(chapter["startAt"] / 1000)
            chapter["end_time"] = datetime.fromtimestamp(chapter["aggregateAt"] / 1000 + 1)
    use_story_bg = detail.event_type != "world_bloom"
    event_bg = await get_img_from_path(ASSETS_BASE_DIR, rqd.event_assets.event_story_bg_path) if use_story_bg else await get_img_from_path(ASSETS_BASE_DIR, rqd.event_assets.event_bg_path)
    event_chara_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.event_assets.event_ban_chara_img) if detail.banner_cid else None
    event_logo = await get_img_from_path(ASSETS_BASE_DIR, rqd.event_assets.event_logo_path)
    ban_chara_icon = await get_img_from_path(ASSETS_BASE_DIR, rqd.event_assets.ban_chara_icon_path)
    h = 1024
    w = min(int(h * 1.6), event_bg.size[0] * h // event_bg.size[1] if event_bg else int(h * 1.6))
    bg = ImageBg(event_bg, blur=False) if event_bg else SEKAI_BLUE_BG
    async def draw(w, h):
        with (Canvas(bg=bg, w=w, h=h).set_padding(BG_PADDING).set_content_align("r") as canvas):
            with Frame().set_size((w-BG_PADDING*2, h-BG_PADDING*2)).set_content_align("lb").set_padding((64, 0)):
                if use_story_bg:
                    ImageBox(event_chara_img, size=(None, int(h * 0.9)), use_alpha_blend=True).set_offset((0, BG_PADDING))
            with VSplit().set_padding(16).set_sep(16).set_item_align("t").set_content_align("t").set_item_bg(roundrect_bg(alpha=80)):
                # logo
                ImageBox(event_logo, size=(None, 150)).set_omit_parent_bg(True)

                # 活动ID和类型和箱活
                with VSplit().set_padding(16).set_sep(12).set_item_align("l").set_content_align("l"):
                    with HSplit().set_padding(0).set_sep(8).set_item_align("l").set_content_align("l"):
                        TextBox(rqd.region.upper(), label_style)
                        TextBox(f"{detail.eid}", text_style)
                        Spacer(w=8)
                        TextBox("类型", label_style)
                        TextBox(f"{detail.event_type}", text_style)
                        if detail.banner_cid:
                            Spacer(w=8)
                            ImageBox(ban_chara_icon, size=(30, 30))
                            TextBox(f"{banner_index}箱", label_style)

                # 活动时间

                with VSplit().set_padding(16).set_sep(12).set_item_align("c").set_content_align("c"):
                    with HSplit().set_padding(0).set_sep(8).set_item_align("lb").set_content_align("lb"):
                        TextBox("开始时间", label_style)
                        TextBox(detail.start_time.strftime("%Y-%m-%d %H:%M:%S"), text_style)
                    with HSplit().set_padding(0).set_sep(8).set_item_align("lb").set_content_align("lb"):
                        TextBox("结束时间", label_style)
                        TextBox(detail.end_time.strftime("%Y-%m-%d %H:%M:%S"), text_style)

                    with HSplit().set_padding(0).set_sep(8).set_item_align("lb").set_content_align("lb"):
                        if detail.start_time <= now <= detail.end_time:
                            TextBox(f"距结束还有{get_readable_timedelta(detail.end_time - now)}", text_style)
                        elif now > detail.end_time:
                            TextBox("活动已结束", text_style)
                        else:
                            TextBox(f"距开始还有{get_readable_timedelta(detail.start_time - now)}", text_style)

                    if detail.event_type == "world_bloom":
                        cur_chapter = None
                        for chapter in wl_chapters:
                            if chapter["start_time"] <= now <= chapter["end_time"]:
                                cur_chapter = chapter
                                break
                        if cur_chapter:
                            TextBox(f"距章节结束还有{get_readable_timedelta(cur_chapter['end_time'] - now)}", text_style)

                    # 进度条
                    progress = (datetime.now() - detail.start_time) / (detail.end_time - detail.start_time)
                    progress = min(max(progress, 0), 1)
                    progress_w, progress_h, border = 320, 8, 1
                    if detail.event_type == "world_bloom" and len(wl_chapters) > 1:
                        with Frame().set_padding(8).set_content_align("lt"):
                            Spacer(w=progress_w+border*2, h=progress_h+border*2).set_bg(RoundRectBg((75, 75, 75, 255), 4))
                            for cid, chapter in enumerate(wl_chapters):
                                cprogress_start = (chapter["start_time"] - detail.start_time) / (detail.end_time - detail.start_time)
                                cprogress_end = (chapter["end_time"] - detail.start_time) / (detail.end_time - detail.start_time)
                                chara_color = color_code_to_rgb(CHARACTER_COLOR_CODE.get(cid))
                                Spacer(w=int(progress_w * (cprogress_end - cprogress_start)), h=progress_h).set_bg(RoundRectBg(chara_color, 4)) \
                                    .set_offset((border + int(progress_w * cprogress_start), border))
                            Spacer(w=int(progress_w * progress), h=progress_h).set_bg(RoundRectBg((255, 255, 255, 200), 4)).set_offset((border, border))
                    else:
                        with Frame().set_padding(8).set_content_align("lt"):
                            Spacer(w=progress_w+border*2, h=progress_h+border*2).set_bg(RoundRectBg((75, 75, 75, 255), 4))
                            Spacer(w=int(progress_w * progress), h=progress_h).set_bg(RoundRectBg((255, 255, 255, 255), 4)).set_offset((border, border))
                # 活动卡片
                event_cards = rqd.event_cards
                if event_cards:
                    with HSplit().set_padding(16).set_sep(16).set_item_align("c").set_content_align("c"):
                        TextBox("活动卡片", label_style)
                        event_cards = event_cards[:8]
                        card_num = len(event_cards)
                        if card_num <= 4: col_count = card_num
                        elif card_num <= 6: col_count = 3
                        else: col_count = 4
                        with Grid(col_count=col_count).set_sep(4, 4):
                            for card, thumb in zip(event_cards, card_thumbs):
                                with VSplit().set_padding(0).set_sep(2).set_item_align("c").set_content_align("c"):
                                    ImageBox(thumb, size=(80, 80))
                                    TextBox(f"ID:{card.card_id}", TextStyle(font=DEFAULT_FONT, size=16, color=(75, 75, 75)), overflow="clip")

                # 加成
                if detail.bonus_attr or detail.bonus_chara_id:
                    with HSplit().set_padding(16).set_sep(8).set_item_align("c").set_content_align("c"):
                        if detail.bonus_attr:
                            TextBox("加成属性", label_style)
                            ImageBox(await get_img_from_path(ASSETS_BASE_DIR, rqd.event_assets.event_attr_image_path), size=(None, 40))
                        if detail:
                            TextBox("加成角色", label_style)
                            bonus_chara_image = []
                            for chara in rqd.event_assets.bonus_chara_path:
                                bonus_chara_image.append(await get_img_from_path(ASSETS_BASE_DIR, chara))
                            with Grid(col_count=5 if len(bonus_chara_image) < 20 else 7).set_sep(4, 4):
                                for image in bonus_chara_image:
                                    ImageBox(image, size=(None, 40))

        add_watermark(canvas)
        return await canvas.get_img()

    return await draw(w, h)

# 合成活动记录图片
async def compose_event_record_image(rqd: EventRecordRequest) -> Image.Image:
    profile = rqd.user_info
    user_events = rqd.event_info
    user_wl_events = rqd.wl_event_info

    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    style2 = TextStyle(font=DEFAULT_FONT, size=16, color=(70, 70, 70))
    style3 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(70, 70, 70))
    style4 = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(50, 50, 50))

    async def draw_events(name, user_events: List[EventHistoryInfo]):
        topk = 30
        if any(item.rank is not None for item in user_events):
            has_rank = True
            title = f"排名前{topk}的{name}记录"
            user_events.sort(key=lambda x: (x.rank if x.rank is not None else float("inf"), -x.event_point))
        else:
            has_rank = False
            title = f"活动点数前{topk}的{name}记录"
            user_events.sort(key=lambda x: -x.event_point)

        user_events = user_events[:topk]

        with VSplit().set_padding(16).set_sep(16).set_item_align("lt").set_content_align("lt").set_bg(roundrect_bg(alpha=80)):
            TextBox(title, style1)

            th, sh, gh = 28, 40, 80
            with HSplit().set_padding(16).set_sep(16).set_item_align("lt").set_content_align("lt").set_bg(roundrect_bg(alpha=80)):
                # 活动信息
                with VSplit().set_padding(0).set_sep(sh).set_item_align("c").set_content_align("c"):
                    TextBox("活动", style1).set_h(th).set_content_align("c")
                    for item in user_events:
                        event_start_at = datetime.fromtimestamp(item.event_start_at / 1000)
                        event_end_at = datetime.fromtimestamp(item.event_end_at / 1000)
                        with HSplit().set_padding(0).set_sep(4).set_item_align("l").set_content_align("l").set_h(gh):
                            if "charaIcon" in item:
                                ImageBox(await get_img_from_path(ASSETS_BASE_DIR, item.wl_chara_icon_path), size=(None, gh))
                            ImageBox(await get_img_from_path(ASSETS_BASE_DIR, item.banner_path), size=(None, gh))
                            with VSplit().set_padding(0).set_sep(2).set_item_align("l").set_content_align("l"):
                                TextBox(f"【{item.event_id}】{item.event_name}", style2).set_w(150)
                                TextBox(f"S {event_start_at.strftime('%Y-%m-%d %H:%M')}", style2)
                                TextBox(f"T {event_end_at.strftime('%Y-%m-%d %H:%M')}", style2)
                # 排名
                if has_rank:
                    with VSplit().set_padding(0).set_sep(sh).set_item_align("c").set_content_align("c"):
                        TextBox("排名", style1).set_h(th).set_content_align("c")
                        for item in user_events:
                            rank_text = f"#{item.rank}" if item.rank is not None else "-"
                            TextBox(rank_text, style3, overflow="clip").set_h(gh).set_content_align("c")
                # 活动点数
                with VSplit().set_padding(0).set_sep(sh).set_item_align("c").set_content_align("c"):
                    TextBox("PT", style1).set_h(th).set_content_align("c")
                    for item in user_events:
                        TextBox(f"{item.event_point}", style3, overflow="clip").set_h(gh).set_content_align("c")

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            await get_detailed_profile_card(rqd.user_info)
            TextBox("每次上传时进行增量更新，未上传过的记录将会丢失", style4).set_bg(roundrect_bg(alpha=80)).set_padding(12)
            with HSplit().set_sep(16).set_item_align("lt").set_content_align("lt"):
                if user_events:
                    await draw_events("活动", user_events)
                if user_wl_events:
                    await draw_events("WL单榜", user_wl_events)

    add_watermark(canvas)
    return await canvas.get_img()

# 合成活动列表图片
async def compose_event_list_image(rqd: EventListRequest) -> Image.Image:
    event_list = rqd.event_info

    row_count = math.ceil(math.sqrt(len(event_list)))
    style1 = TextStyle(font=DEFAULT_HEAVY_FONT, size=10, color=(50, 50, 50))
    style2 = TextStyle(font=DEFAULT_FONT, size=10, color=(70, 70, 70))
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_padding(0).set_sep(4).set_content_align('lt').set_item_align('lt'):
            TextBox(
                f"活动按时间顺序排列，黄色为当期活动，灰色为过去活动",
                TextStyle(font=DEFAULT_FONT, size=12, color=(0, 0, 100))
            ).set_bg(roundrect_bg(radius=4)).set_padding(4)
            with Grid(row_count=row_count, vertical=True).set_sep(6, 6).set_item_align('lt').set_content_align('lt'):
                for d in event_list:
                    now = datetime.now()
                    event_start_at = datetime.fromtimestamp(d.event_start_at / 1000)
                    event_end_at = datetime.fromtimestamp(d.event_end_at / 1000)
                    bg_color = WIDGET_BG_COLOR
                    if event_start_at <= now <= event_end_at:
                        bg_color = (255, 250, 220, 200)
                    elif now > event_end_at:
                        bg_color = (220, 220, 220, 200)
                    bg = roundrect_bg(bg_color, 5, alpha=180)

                    with HSplit().set_padding(4).set_sep(4).set_item_align('lt').set_content_align('lt').set_bg(bg):
                        with VSplit().set_padding(0).set_sep(2).set_item_align('lt').set_content_align('lt'):
                            ImageBox(await get_img_from_path(ASSETS_BASE_DIR, d.event_banner_path), size=(None, 40))
                            with Grid(col_count=3).set_padding(0).set_sep(1, 1):
                                if d.event_cards:
                                    for thumb in d.event_cards:
                                        ImageBox(await get_card_full_thumbnail(thumb), size=(30, 30))
                            if not d.event_cards:
                                Spacer(h=60)
                            if d.event_cards:
                                if len(d.event_cards) <= 3:
                                    Spacer(h=29)
                        with VSplit().set_padding(0).set_sep(2).set_item_align('lt').set_content_align('lt'):
                            TextBox(f"{d.event_name}", style1, line_count=2, use_real_line_count=False).set_w(100)
                            TextBox(f"ID: {d.event_id} {d.event_type}", style2)
                            TextBox(f"S {event_start_at.strftime('%Y-%m-%d %H:%M')}", style2)
                            TextBox(f"T {event_end_at.strftime('%Y-%m-%d %H:%M')}", style2)
                            with HSplit().set_padding(0).set_sep(4):
                                if d.event_attr_path: ImageBox(await get_img_from_path(ASSETS_BASE_DIR, d.event_attr_path), size=(None, 24))
                                if d.event_unit_path: ImageBox(await get_img_from_path(ASSETS_BASE_DIR, d.event_unit_path), size=(None, 24))
                                if d.event_chara_path: ImageBox(await get_img_from_path(ASSETS_BASE_DIR, d.event_chara_path), size=(None, 24))
                                if not (d.event_attr_path or d.event_unit_path or d.event_chara_path):
                                    Spacer(w=24, h=24)

    add_watermark(canvas)

    return await canvas.get_img()