from datetime import datetime
from PIL import Image
from typing import List, Optional, Dict, Any
from pydantic import BaseModel

from src.base.configs import ASSETS_BASE_DIR
from src.base.plot import(
    ImageBg,
    RoundRectBg,
    Canvas,
    Frame,
    HSplit,
    VSplit,
    Grid,
    TextStyle,
    TextBox,
    ImageBox,
    Spacer,
)

from src.base.painter import DEFAULT_FONT, DEFAULT_BOLD_FONT, color_code_to_rgb
from src.base.draw import SEKAI_BLUE_BG, BG_PADDING, roundrect_bg, add_watermark, CHARACTER_COLOR_CODE
from src.base.utils import get_readable_timedelta, get_img_from_path

class EventInfo(BaseModel):
    eid: str
    event_type: str
    event_type_name: str
    start_time: Any
    end_time: Any
    is_wl_event: bool
    wl_time_list: Optional[List[Dict[str, Any]]]

class EventAsset(BaseModel):
    event_bg: str
    event_logo: str
    event_attr_image: str
    event_story_bg: str
    bonus_chara: Optional[List[str]]

class EventCard(BaseModel):
    id: int
    thumb: str
    attr_image: str

class EventDetailRequest(BaseModel):
    region: str
    event_info: EventInfo
    event_asset: EventAsset
    event_cards: List[EventCard]

async def compose_event_detail_image(rqd: EventDetailRequest) -> Image.Image:
    detail = rqd.event_info
    now = datetime.now()

    if detail.banner_cid:
        banner_index = rqd.event_info.banner_index

    label_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    text_style = TextStyle(font=DEFAULT_FONT, size=24, color=(70, 70, 70))

    wl_chapters = rqd.event_info.wl_time_list
    if rqd.event_info.is_wl_event:
        for chapter in wl_chapters:
            chapter["start_time"] = datetime.fromtimestamp(chapter["startAt"] / 1000)
            chapter["end_time"] = datetime.fromtimestamp(chapter["aggregateAt"] / 1000 + 1)

    use_story_bg = detail.event_story_bg and detail.event_ban_chara_img and detail.etype != "world_bloom"
    event_bg = detail.event_story_bg if use_story_bg else detail.event_bg
    h = 1024
    w = min(int(h * 1.6), event_bg.size[0] * h // event_bg.size[1] if event_bg else int(h * 1.6))
    bg = ImageBg(event_bg, blur=False) if event_bg else SEKAI_BLUE_BG

    async def draw(w, h):
        with (Canvas(bg=bg, w=w, h=h).set_padding(BG_PADDING).set_content_align("r") as canvas):
            with Frame().set_size((w-BG_PADDING*2, h-BG_PADDING*2)).set_content_align("lb").set_padding((64, 0)):
                if use_story_bg:
                    ImageBox(detail.event_ban_chara_img, size=(None, int(h * 0.9)), use_alphablend=True).set_offset((0, BG_PADDING))

            with VSplit().set_padding(16).set_sep(16).set_item_align("t").set_content_align("t").set_item_bg(roundrect_bg()):
                # logo
                ImageBox(detail.event_logo, size=(None, 150)).set_omit_parent_bg(True)

                # 活动ID和类型和箱活
                with VSplit().set_padding(16).set_sep(12).set_item_align("l").set_content_align("l"):
                    with HSplit().set_padding(0).set_sep(8).set_item_align("l").set_content_align("l"):
                        TextBox(rqd.region.upper(), label_style)
                        TextBox(f"{detail.eid}", text_style)
                        Spacer(w=8)
                        TextBox("类型", label_style)
                        TextBox(f"{detail.etype_name}", text_style)
                        if detail.banner_cid:
                            Spacer(w=8)
                            ImageBox(await get_img_from_path(rqd.event_info.bonus_attr))
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

                    if detail.etype == "world_bloom":
                        cur_chapter = None
                        for chapter in wl_chapters:
                            if chapter["start_time"] <= now <= chapter["end_time"]:
                                cur_chapter = chapter
                                break
                        if cur_chapter:
                            TextBox(f"距章节结束还有{await get_readable_timedelta(cur_chapter['end_time'] - now)}", text_style)

                    # 进度条
                    progress = (datetime.now() - detail.start_time) / (detail.end_time - detail.start_time)
                    progress = min(max(progress, 0), 1)
                    progress_w, progress_h, border = 320, 8, 1
                    if detail.etype == "world_bloom" and len(wl_chapters) > 1:
                        with Frame().set_padding(8).set_content_align("lt"):
                            Spacer(w=progress_w+border*2, h=progress_h+border*2).set_bg(RoundRectBg((75, 75, 75, 255), 4))
                            for i, chapter in enumerate(wl_chapters):
                                cprogress_start = (chapter["start_time"] - detail.start_time) / (detail.end_time - detail.start_time)
                                cprogress_end = (chapter["end_time"] - detail.start_time) / (detail.end_time - detail.start_time)
                                chapter_cid = chapter["wl_cid"]
                                chara_color = color_code_to_rgb(CHARACTER_COLOR_CODE.get(chapter_cid))
                                Spacer(w=int(progress_w * (cprogress_end - cprogress_start)), h=progress_h).set_bg(RoundRectBg(chara_color, 4)) \
                                    .set_offset((border + int(progress_w * cprogress_start), border))
                            Spacer(w=int(progress_w * progress), h=progress_h).set_bg(RoundRectBg((255, 255, 255, 200), 4)).set_offset((border, border))
                    else:
                        with Frame().set_padding(8).set_content_align("lt"):
                            Spacer(w=progress_w+border*2, h=progress_h+border*2).set_bg(RoundRectBg((75, 75, 75, 255), 4))
                            Spacer(w=int(progress_w * progress), h=progress_h).set_bg(RoundRectBg((255, 255, 255, 255), 4)).set_offset((border, border))

                # 活动卡片
                if detail.event_cards:
                    with HSplit().set_padding(16).set_sep(16).set_item_align("c").set_content_align("c"):
                        TextBox("活动卡片", label_style)
                        detail.event_cards = detail.event_cards[:8]
                        card_num = len(detail.event_cards)
                        if card_num <= 4: col_count = card_num
                        elif card_num <= 6: col_count = 3
                        else: col_count = 4
                        with Grid(col_count=col_count).set_sep(4, 4):
                            for card, thumb in zip(detail.event_cards, detail.event_card_thumbs):
                                with VSplit().set_padding(0).set_sep(2).set_item_align("c").set_content_align("c"):
                                    ImageBox(thumb, size=(80, 80))
                                    TextBox(f"ID:{card['id']}", TextStyle(font=DEFAULT_FONT, size=16, color=(75, 75, 75)), overflow="clip")

                # 加成
                if detail.bonus_attr or detail.bonus_cuids:
                    with HSplit().set_padding(16).set_sep(8).set_item_align("c").set_content_align("c"):
                        if detail.bonus_attr:
                            TextBox("加成属性", label_style)
                            ImageBox(await get_img_from_path(ASSETS_BASE_DIR, rqd.event_asset.event_attr_image), size=(None, 40))
                        if detail.bonus_cuids:
                            TextBox("加成角色", label_style)
                            bonus_chara_image = []
                            for chara in rqd.event_asset.bonus_chara:
                                bonus_chara_image.append(await get_img_from_path(ASSETS_BASE_DIR, chara))
                            with Grid(col_count=5 if len(bonus_chara_image) < 20 else 7).set_sep(4, 4):
                                for image in bonus_chara_image:
                                    ImageBox(image, size=(None, 40))

        add_watermark(canvas)
        return await canvas.get_img()

    return await draw(w, h)
