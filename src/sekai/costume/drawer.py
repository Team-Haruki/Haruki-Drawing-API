import asyncio
import logging

from PIL import Image

from src.sekai.base.draw import BG_PADDING, SEKAI_BLUE_BG, add_request_watermark, roundrect_bg
from src.sekai.base.painter import BLACK, DEFAULT_BOLD_FONT, DEFAULT_FONT, get_font, get_font_desc, get_text_size
from src.sekai.base.plot import Canvas, Frame, Grid, HSplit, ImageBox, Spacer, TextBox, TextStyle, VSplit
from src.sekai.base.timezone import datetime_from_millis
from src.sekai.base.utils import get_img_from_path
from src.settings import ASSETS_BASE_DIR

from .model import CostumeDetailRequest, CostumeListRequest

logger = logging.getLogger(__name__)

PART_COLORS = {
    "body": (255, 250, 220, 200),
    "head": (235, 248, 255, 200),
    "hair": (245, 235, 255, 200),
}
PART_LABELS = {
    "body": "服装",
    "head": "饰品",
    "hair": "发型",
}
PART_ORDER = ("body", "head", "hair")
LIST_COL_COUNT = 12
LIST_ITEM_WIDTH = 92
LIST_ITEM_HEIGHT = 106
LIST_THUMB_SIZE = 34
LIST_GRID_PADDING = 10
LIST_GRID_SEP = 4
LIST_PANEL_WIDTH = LIST_COL_COUNT * LIST_ITEM_WIDTH + (LIST_COL_COUNT - 1) * LIST_GRID_SEP + LIST_GRID_PADDING * 2
COSTUME_DETAIL_PREVIEW_SIZE = (420, 520)
PREVIEW_FOREGROUND_DETECT_WIDTH = 700


async def _load_image(path: str | None) -> Image.Image:
    return await get_img_from_path(ASSETS_BASE_DIR, path, on_missing="placeholder")


async def _load_optional_image(path: str | None) -> Image.Image | None:
    if not path:
        return None
    return await get_img_from_path(ASSETS_BASE_DIR, path, on_missing="placeholder")


def _preview_foreground_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    source = image.convert("RGBA")
    alpha_bbox = source.getchannel("A").getbbox()
    if alpha_bbox and alpha_bbox != (0, 0, source.width, source.height):
        return alpha_bbox

    scale = min(1.0, PREVIEW_FOREGROUND_DETECT_WIDTH / source.width)
    small_size = (max(1, round(source.width * scale)), max(1, round(source.height * scale)))
    sample = source.resize(small_size, Image.Resampling.BILINEAR) if small_size != source.size else source
    pixels = sample.load()
    width, height = sample.size
    edge_width = max(2, round(width * 0.02))
    threshold = 36
    min_col_hits = max(2, round(height * 0.015))
    min_row_hits = max(2, round(width * 0.015))
    col_hits = [0] * width
    row_hits = [0] * height

    for y in range(height):
        edge_pixels = []
        for x in range(edge_width):
            edge_pixels.append(pixels[x, y])
            edge_pixels.append(pixels[width - 1 - x, y])
        bg_r = sum(item[0] for item in edge_pixels) // len(edge_pixels)
        bg_g = sum(item[1] for item in edge_pixels) // len(edge_pixels)
        bg_b = sum(item[2] for item in edge_pixels) // len(edge_pixels)

        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a > 16 and max(abs(r - bg_r), abs(g - bg_g), abs(b - bg_b)) > threshold:
                col_hits[x] += 1
                row_hits[y] += 1

    xs = [idx for idx, hits in enumerate(col_hits) if hits >= min_col_hits]
    ys = [idx for idx, hits in enumerate(row_hits) if hits >= min_row_hits]
    if not xs or not ys:
        return None

    inv_scale = 1.0 / scale
    return (
        max(0, round(min(xs) * inv_scale)),
        max(0, round(min(ys) * inv_scale)),
        min(source.width, round((max(xs) + 1) * inv_scale)),
        min(source.height, round((max(ys) + 1) * inv_scale)),
    )


def _costume_preview_cover_crop_box(
    image: Image.Image,
    target_size: tuple[int, int] = COSTUME_DETAIL_PREVIEW_SIZE,
) -> tuple[int, int, int, int]:
    width, height = image.size
    target_w, target_h = target_size
    target_aspect = target_w / target_h
    source_aspect = width / height
    bbox = _preview_foreground_bbox(image)

    if source_aspect > target_aspect:
        crop_h = height
        crop_w = min(width, max(1, round(height * target_aspect)))
        center_x = (bbox[0] + bbox[2]) / 2 if bbox else width / 2
        left = round(center_x - crop_w / 2)
        left = max(0, min(left, width - crop_w))
        return (left, 0, left + crop_w, height)

    crop_w = width
    crop_h = min(height, max(1, round(width / target_aspect)))
    center_y = (bbox[1] + bbox[3]) / 2 if bbox else height / 2
    top = round(center_y - crop_h / 2)
    top = max(0, min(top, height - crop_h))
    return (0, top, width, top + crop_h)


def _prepare_costume_preview_image(
    image: Image.Image,
    target_size: tuple[int, int] = COSTUME_DETAIL_PREVIEW_SIZE,
) -> Image.Image:
    crop_box = _costume_preview_cover_crop_box(image, target_size)
    return image.convert("RGBA").crop(crop_box).resize(target_size, Image.Resampling.LANCZOS)


def _format_time(value: int | None, timezone: str) -> str:
    if not value:
        return "-"
    return datetime_from_millis(value, timezone).strftime("%Y-%m-%d %H:%M")


def _source_cards_text(ids: list[int]) -> str:
    if not ids:
        return "-"
    if len(ids) <= 6:
        return ", ".join(str(i) for i in ids)
    return ", ".join(str(i) for i in ids[:6]) + f" 等{len(ids)}张"


def _published_time_text(costume, timezone: str) -> str:
    if not costume.published_at:
        return "-"
    return _format_time(costume.published_at, timezone)


def _character_3d_ids_text(ids: list[int]) -> str:
    if not ids:
        return "-"
    if ids == list(range(ids[0], ids[-1] + 1)) and len(ids) > 2:
        return f"{ids[0]}-{ids[-1]}"
    return ",".join(str(item) for item in ids)


def _costume_lookup_text(costume) -> str:
    role_ids = costume.character_3d_ids
    if costume.character_3d_id:
        role_ids = [costume.character_3d_id]
    role_text = _character_3d_ids_text(role_ids)
    if costume.outfit_id:
        return f"服{costume.outfit_id} 角{role_text}"
    if costume.accessory_id:
        return f"饰{costume.accessory_id} 角{role_text}"
    if costume.hair_id:
        return f"发{costume.hair_id} 角{role_text}"
    return f"ID:{costume.costume_id}"


def _preview_placeholder(label: str, size: tuple[int, int] = (420, 520)) -> None:
    def draw(_, p):
        text_style = get_font_desc(DEFAULT_BOLD_FONT, 28)
        sub_style = get_font_desc(DEFAULT_FONT, 18)
        title_font = get_font(DEFAULT_BOLD_FONT, 28)
        sub_font = get_font(DEFAULT_FONT, 18)
        title = "3D 预览"
        subtitle = "等待渲染服务接入"
        title_w, _ = get_text_size(title_font, title)
        sub_w, _ = get_text_size(sub_font, subtitle)
        p.roundrect((18, 18), (p.w - 18, p.h - 18), (255, 255, 255, 75), 10, (210, 215, 235, 180), 2)
        p.text(title, ((p.w - title_w) // 2, p.h // 2 - 42), font=text_style, fill=(70, 70, 90, 255))
        p.text(subtitle, ((p.w - sub_w) // 2, p.h // 2), font=sub_style, fill=(110, 110, 130, 255))
        if label:
            label_style = get_font_desc(DEFAULT_FONT, 16)
            label_font = get_font(DEFAULT_FONT, 16)
            label_w, _ = get_text_size(label_font, label)
            p.text(label, ((p.w - label_w) // 2, p.h // 2 + 34), font=label_style, fill=(130, 130, 150, 255))

    Frame().set_size(size).set_bg(roundrect_bg(fill=(255, 255, 255, 65), radius=10)).add_draw_func(draw)


def _draw_info_row(label: str, value: str, width: int = 620) -> None:
    label_style = TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=(50, 50, 50))
    value_style = TextStyle(font=DEFAULT_FONT, size=22, color=(70, 70, 70))
    with HSplit().set_padding(0).set_sep(10).set_content_align("l").set_item_align("lt"):
        TextBox(label, label_style).set_w(110)
        TextBox(value or "-", value_style, use_real_line_count=True, overflow="shrink").set_w(width - 120)


def _costume_list_sections(costumes: list) -> list[tuple[str, list]]:
    grouped = {}
    order = []
    for item in costumes:
        part_type = item.part_type or ""
        if part_type not in grouped:
            grouped[part_type] = []
            order.append(part_type)
        grouped[part_type].append(item)
    ordered = [part for part in PART_ORDER if part in grouped]
    ordered.extend(part for part in order if part not in ordered)
    return [(part, grouped[part]) for part in ordered]


async def compose_costume_list_image(rqd: CostumeListRequest) -> Image.Image:
    thumbs = await asyncio.gather(*[_load_image(item.thumbnail_path) for item in rqd.costumes])
    thumbs_by_id = {item.costume_id: thumb for item, thumb in zip(rqd.costumes, thumbs)}
    title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=BLACK)
    section_style = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(55, 55, 55))
    name_style = TextStyle(font=DEFAULT_BOLD_FONT, size=12, color=BLACK)
    id_style = TextStyle(font=DEFAULT_BOLD_FONT, size=13, color=(55, 55, 55))

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_sep(10).set_content_align("lt").set_item_align("lt"):
            if not rqd.costumes:
                TextBox("没有匹配的服装", title_style).set_padding(20).set_bg(roundrect_bg(alpha=80))
            else:
                sections = _costume_list_sections(rqd.costumes)
                show_sections = len(sections) > 1
                for part_type, items in sections:
                    with VSplit().set_sep(6).set_content_align("lt").set_item_align("lt"):
                        if show_sections:
                            TextBox(f"{PART_LABELS.get(part_type, part_type or '-')}  {len(items)}", section_style)
                        with (
                            Grid(col_count=LIST_COL_COUNT)
                            .set_bg(roundrect_bg(alpha=80))
                            .set_padding(LIST_GRID_PADDING)
                            .set_sep(LIST_GRID_SEP, LIST_GRID_SEP)
                        ):
                            for item in items:
                                thumb = thumbs_by_id[item.costume_id]
                                bg = roundrect_bg(fill=PART_COLORS.get(item.part_type, (255, 255, 255, 200)), radius=6)
                                with (
                                    Frame()
                                    .set_size((LIST_ITEM_WIDTH, LIST_ITEM_HEIGHT))
                                    .set_content_align("c")
                                    .set_bg(bg)
                                ):
                                    with VSplit().set_padding(6).set_sep(3).set_content_align("c").set_item_align("c"):
                                        ImageBox(
                                            thumb,
                                            size=(LIST_THUMB_SIZE, LIST_THUMB_SIZE),
                                            image_size_mode="fit",
                                            shadow=True,
                                        )
                                        TextBox(item.name, name_style, line_count=2, overflow="shrink").set_w(
                                            LIST_ITEM_WIDTH - 12
                                        ).set_content_align("c")
                                        TextBox(
                                            _costume_lookup_text(item),
                                            id_style,
                                        ).set_w(LIST_ITEM_WIDTH - 12).set_content_align("c")

    add_request_watermark(canvas, rqd)
    return await canvas.get_img()


async def compose_costume_detail_image(rqd: CostumeDetailRequest) -> Image.Image:
    costume = rqd.costume
    variant_images = await asyncio.gather(*[_load_image(item.thumbnail_path) for item in costume.variants])
    preview = await _load_optional_image(costume.preview_image_path)

    title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=28, color=BLACK)
    label_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    small_style = TextStyle(font=DEFAULT_FONT, size=18, color=(70, 70, 70))
    variant_id_style = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(55, 55, 55))
    variant_color_style = TextStyle(font=DEFAULT_FONT, size=17, color=(80, 80, 80))

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with HSplit().set_sep(16).set_content_align("lt").set_item_align("lt"):
            with VSplit().set_padding(16).set_sep(0).set_bg(roundrect_bg(alpha=80)).set_item_align("c"):
                if preview:
                    ImageBox(_prepare_costume_preview_image(preview), size=COSTUME_DETAIL_PREVIEW_SIZE, shadow=True)
                else:
                    _preview_placeholder(f"costume_id={costume.costume_id}", COSTUME_DETAIL_PREVIEW_SIZE)

            with VSplit().set_sep(16).set_content_align("lt").set_item_align("lt"):
                with VSplit().set_padding(16).set_sep(8).set_bg(roundrect_bg(alpha=80)).set_item_align("lt"):
                    TextBox(costume.name, title_style, use_real_line_count=True).set_w(660)
                    if costume.outfit_id:
                        _draw_info_row("服装ID", str(costume.outfit_id))
                    elif costume.accessory_id:
                        _draw_info_row("饰品ID", str(costume.accessory_id))
                    elif costume.hair_id:
                        _draw_info_row("发型ID", str(costume.hair_id))
                    else:
                        _draw_info_row("ID", str(costume.costume_id))
                    role_ids = costume.character_3d_ids
                    if costume.character_3d_id:
                        role_ids = [costume.character_3d_id]
                    _draw_info_row("角色ID", _character_3d_ids_text(role_ids))
                    _draw_info_row("类别", costume.part_name or costume.part_type)
                    _draw_info_row("角色", costume.character_name)
                    _draw_info_row("颜色", costume.color_name or "-")
                    _draw_info_row("获得", costume.how_to_obtain or "-")
                    _draw_info_row("设计", costume.designer or "-")
                    _draw_info_row("来源卡", _source_cards_text(costume.source_card_ids))
                    _draw_info_row("发布", _published_time_text(costume, rqd.timezone))

                with VSplit().set_padding(16).set_sep(10).set_bg(roundrect_bg(alpha=80)).set_item_align("lt"):
                    TextBox("颜色缩略图 / 颜色ID", label_style)
                    if not costume.variants:
                        TextBox("没有颜色变体", small_style)
                    else:
                        with Grid(col_count=5).set_sep(8, 8):
                            for variant, img in zip(costume.variants, variant_images):
                                with (
                                    VSplit()
                                    .set_padding(8)
                                    .set_sep(4)
                                    .set_bg(roundrect_bg(alpha=80))
                                    .set_item_align("c")
                                ):
                                    ImageBox(img, size=(56, 56), image_size_mode="fit", shadow=True)
                                    TextBox(f"颜色{variant.color_id}", variant_id_style).set_w(112).set_content_align(
                                        "c"
                                    )
                                    TextBox(
                                        variant.color_name or f"Color {variant.color_id}",
                                        variant_color_style,
                                        line_count=2,
                                        overflow="shrink",
                                    ).set_w(112).set_content_align("c")

                Spacer(h=2)

    add_request_watermark(canvas, rqd)
    return await canvas.get_img()
