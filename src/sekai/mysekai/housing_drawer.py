import asyncio
import base64
from io import BytesIO

from PIL import Image

from src.sekai.base.draw import BG_PADDING, SEKAI_BLUE_BG, add_request_watermark, roundrect_bg
from src.sekai.base.plot import Canvas, HSplit, ImageBox, TextBox, TextStyle, VSplit
from src.sekai.base.utils import get_img_from_path, run_in_pool
from src.sekai.mysekai.model import MysekaiHousingCompetitionEntry, MysekaiHousingCompetitionRequest
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT, DEFAULT_HEAVY_FONT

CARD_WIDTH = 680
CARD_INNER_WIDTH = CARD_WIDTH - 24
THUMBNAIL_SIZE = (CARD_INNER_WIDTH, 360)
HEADER_BANNER_SIZE = (210, 86)


async def compose_mysekai_housing_competition_image(rqd: MysekaiHousingCompetitionRequest) -> Image.Image:
    banner_task = _load_optional_image(rqd.banner_image_base64, rqd.banner_image_path)
    entry_tasks = [
        _load_optional_image(entry.thumbnail_image_base64, entry.thumbnail_path)
        for entry in rqd.entries[:5]
    ]
    banner, *entry_images = await asyncio.gather(banner_task, *entry_tasks)

    title_style = TextStyle(font=DEFAULT_HEAVY_FONT, size=30, color=(35, 35, 35, 255))
    subtitle_style = TextStyle(font=DEFAULT_FONT, size=18, color=(70, 70, 70, 255))
    small_style = TextStyle(font=DEFAULT_FONT, size=16, color=(80, 80, 80, 255))
    owner_style = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(45, 45, 45, 255))
    name_style = TextStyle(font=DEFAULT_FONT, size=17, color=(55, 55, 55, 255))
    rank_style = TextStyle(font=DEFAULT_HEAVY_FONT, size=28, color=(35, 35, 35, 255))
    meta_style = TextStyle(font=DEFAULT_FONT, size=16, color=(70, 70, 70, 255))

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_sep(10).set_content_align("lt").set_item_align("lt"):
            with VSplit().set_w(CARD_WIDTH).set_padding(12).set_sep(8).set_bg(roundrect_bg(alpha=80)):
                with HSplit().set_sep(12).set_content_align("lt").set_item_align("lt"):
                    ImageBox(banner, size=HEADER_BANNER_SIZE, image_size_mode="fill", shadow=True)
                    with VSplit().set_sep(4).set_content_align("lt").set_item_align("lt"):
                        TextBox(rqd.name, title_style, line_count=2, overflow="shrink").set_w(420)
                        if rqd.description:
                            TextBox(rqd.description, subtitle_style, line_count=2, overflow="shrink").set_w(420)
                        meta = f"{rqd.region.upper()}-{rqd.competition_id}"
                        meta += f"  统计 {rqd.unique_count} 个投稿"
                        TextBox(meta, small_style, overflow="shrink").set_w(420)

            entries = list(rqd.entries[:5])
            if not entries:
                TextBox("没有采样到可显示的百景投稿", title_style).set_w(CARD_WIDTH).set_padding(18).set_bg(
                    roundrect_bg(alpha=80)
                )
            for entry, image in zip(entries, entry_images, strict=False):
                _entry_block(entry, image, owner_style, name_style, rank_style, meta_style)

    add_request_watermark(canvas, rqd)
    return await canvas.get_img()


async def _load_optional_image(image_base64: str | None, image_path: str | None) -> Image.Image:
    if image_base64 and image_base64.strip():
        return await run_in_pool(_decode_base64_image, image_base64)
    return await get_img_from_path(ASSETS_BASE_DIR, image_path, on_missing="placeholder")


def _decode_base64_image(data: str) -> Image.Image:
    payload = data.strip()
    if payload.lower().startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]
    raw = base64.b64decode(payload)
    with Image.open(BytesIO(raw)) as img:
        img.load()
        return img.convert("RGBA")


def _entry_block(
    entry: MysekaiHousingCompetitionEntry,
    image: Image.Image,
    owner_style: TextStyle,
    name_style: TextStyle,
    rank_style: TextStyle,
    meta_style: TextStyle,
) -> None:
    with VSplit().set_w(CARD_WIDTH).set_padding(12).set_sep(7).set_bg(roundrect_bg(alpha=80)).set_item_align("lt"):
        TextBox(_owner_line(entry), owner_style, overflow="shrink").set_w(CARD_INNER_WIDTH)
        TextBox(_work_line(entry), name_style, line_count=2, overflow="shrink").set_w(CARD_INNER_WIDTH)
        ImageBox(image, size=THUMBNAIL_SIZE, image_size_mode="fill", shadow=True)
        TextBox(f"点赞数 {entry.review_count}，排名 {entry.rank}", rank_style).set_w(CARD_INNER_WIDTH)
        with HSplit().set_sep(12).set_content_align("lt").set_item_align("lt"):
            previous_text = _neighbor_text("上一名", entry.previous_review_count, entry.previous_delta, "还差")
            TextBox(previous_text, meta_style).set_w(322)
            next_text = _neighbor_text("下一名", entry.next_review_count, entry.next_delta, "领先")
            TextBox(next_text, meta_style).set_w(322)
        if entry.word:
            TextBox(entry.word, meta_style, line_count=2, overflow="shrink").set_w(CARD_INNER_WIDTH)


def _owner_line(entry: MysekaiHousingCompetitionEntry) -> str:
    owner = str(entry.owner_user_name or "").strip()
    return owner or "匿名玩家"


def _work_line(entry: MysekaiHousingCompetitionEntry) -> str:
    name = str(entry.name or "").strip() or "未命名投稿"
    return f"作品: {name}"


def _neighbor_text(label: str, score: int | None, delta: int | None, verb: str) -> str:
    if score is None:
        return f"{label} 无"
    return f"{label} {score}，{verb} {max(0, int(delta or 0))}"
