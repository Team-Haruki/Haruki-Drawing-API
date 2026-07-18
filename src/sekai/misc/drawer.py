import asyncio
from dataclasses import dataclass
from functools import partial
import logging
import re
import time

from PIL import Image, ImageDraw, ImageFilter

from src.core.heavy_render_pool import EncodedImagePayload
from src.sekai.base.draw import (
    BG_PADDING,
    CHARACTER_COLOR_CODE,
    SEKAI_BLUE_BG,
    Canvas,
    TextBox,
    add_request_watermark,
    roundrect_bg,
)
from src.sekai.base.painter import ADAPTIVE_WB, WHITE, color_code_to_rgb, get_font, get_text_size
from src.sekai.base.plot import (
    Flow,
    Frame,
    Grid,
    HSplit,
    ImageBg,
    ImageBox,
    RoundRectBg,
    Spacer,
    TextStyle,
    VSplit,
    Widget,
)
from src.sekai.base.timezone import datetime_from_millis
from src.sekai.base.utils import (
    ImageSource,
    get_asset_image_ref,
    get_img_from_path,
    get_img_resized,
    get_str_display_length,
    run_in_pool,
)
from src.sekai.skia_renderer.canvas import (
    render_canvas_payload,
    skia_plot_enabled,
)
from src.settings import (
    ASSETS_BASE_DIR,
    DEFAULT_BOLD_FONT,
    DEFAULT_FONT,
    DEFAULT_HEAVY_FONT,
)

# =========================== 从.model导入数据类型 =========================== #
from .model import AliasListRequest, BirthdayEventTime, CharaBirthdayRequest, CommandHelpRenderRequest

logger = logging.getLogger(__name__)
_birthday_perf_logger = logging.getLogger("misc.birthday.perf")

# =========================== 颜色常量 =========================== #

BLACK = (0, 0, 0, 255)
_HELP_IMAGE_WIDTH = 1080
_HELP_MARGIN = 62
_HELP_CARD_MARGIN = 28
_HELP_MAX_TEXT_WIDTH = _HELP_IMAGE_WIDTH - _HELP_MARGIN * 2
_HELP_LINK_RE = re.compile(r"\[([^\]]+)]\([^)]+\)")
_ALIAS_TRIM_ALPHA_FLOOR = 36
_ALIAS_TRIM_MIN_FRAME_W = 260
_ALIAS_TRIM_MAX_FRAME_W = 920
_ALIAS_TRIM_MIN_OVERLAP = 32
_ALIAS_TRIM_MAX_OVERLAP = 128
_ALIAS_TRIM_MIN_DISPLAY_H = 460
_ALIAS_TRIM_BOTTOM_OVERFLOW = 24
_BIRTHDAY_CARD_THUMB_SIZE = 80
_BIRTHDAY_CALENDAR_ICON_SIZE = 40


@dataclass(frozen=True)
class _CommandHelpLine:
    text: str
    font_name: str
    size: int
    indent: int = 0
    fill: tuple[int, int, int, int] = (50, 61, 78, 255)
    bg: tuple[int, int, int, int] | None = None
    gap_before: int = 0
    label: str = ""
    label_width: int = 0


@dataclass(frozen=True)
class _CommandHelpSection:
    title: str
    lines: list[_CommandHelpLine]


def _command_help_line_height(size: int) -> int:
    return max(18, int(size * 1.55))


def _clean_command_help_inline(text: str) -> str:
    text = _HELP_LINK_RE.sub(r"\1", text)
    text = text.replace("`", "")
    text = text.replace("**", "").replace("__", "")
    text = text.replace("\\", "")
    return text.strip()


def _command_help_heading(line: str) -> tuple[str, int] | None:
    match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
    if not match:
        return None
    return match.group(2), len(match.group(1))


def _command_help_bullet(line: str) -> str | None:
    match = re.match(r"^[-*+]\s+(.+?)\s*$", line)
    if not match:
        return None
    return match.group(1)


def _command_help_numbered(line: str) -> str | None:
    match = re.match(r"^(\d+[.)])\s+(.+?)\s*$", line)
    if not match:
        return None
    return f"{match.group(1)} {match.group(2)}"


def _wrap_command_help_text(font_name: str, size: int, text: str, max_width: int) -> list[str]:
    text = text.strip()
    if not text:
        return [""]

    font = get_font(font_name, size)
    lines: list[str] = []
    current = ""
    for char in text:
        if char == "\t":
            char = " "
        candidate = current + char
        if current and get_text_size(font, candidate)[0] > max_width:
            lines.append(current.rstrip())
            current = "" if char == " " else char
            continue
        current = candidate
    if current.strip():
        lines.append(current.rstrip())
    return lines or [text]


def _append_command_help_wrapped_line(
    lines: list[_CommandHelpLine],
    text: str,
    *,
    font_name: str,
    size: int,
    indent: int = 0,
    fill: tuple[int, int, int, int],
    bg: tuple[int, int, int, int] | None = None,
    gap_before: int = 0,
) -> None:
    text = text.rstrip()
    if not text:
        lines.append(_CommandHelpLine("", font_name, size, indent, fill, bg, gap_before))
        return
    for idx, part in enumerate(_wrap_command_help_text(font_name, size, text, _HELP_MAX_TEXT_WIDTH - indent)):
        lines.append(
            _CommandHelpLine(
                text=part,
                font_name=font_name,
                size=size,
                indent=indent,
                fill=fill,
                bg=bg,
                gap_before=gap_before if idx == 0 else 0,
            )
        )


def _append_command_help_definition_line(
    lines: list[_CommandHelpLine],
    text: str,
    *,
    size: int = 21,
    indent: int = 24,
    label_width: int = 190,
    gap_before: int = 7,
) -> None:
    label, sep, description = text.partition("：")
    if not sep:
        label, sep, description = text.partition(":")
    label = label.strip()
    description = description.strip()
    if not label or not description:
        _append_command_help_wrapped_line(
            lines,
            text,
            font_name=DEFAULT_FONT,
            size=size,
            indent=indent,
            fill=(50, 61, 78, 255),
            gap_before=gap_before,
        )
        return

    wrapped = _wrap_command_help_text(DEFAULT_FONT, size, description, _HELP_MAX_TEXT_WIDTH - indent - label_width)
    for idx, part in enumerate(wrapped):
        lines.append(
            _CommandHelpLine(
                text=part,
                font_name=DEFAULT_FONT,
                size=size,
                indent=indent,
                fill=(50, 61, 78, 255),
                gap_before=gap_before if idx == 0 else 2,
                label=label if idx == 0 else "",
                label_width=label_width,
            )
        )


def _strip_command_help_frontmatter(markdown: str) -> str:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return markdown
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[idx + 1 :])
    return markdown


def _strip_command_help_output_section(markdown: str) -> str:
    kept: list[str] = []
    skipping = False
    skip_level = 0
    for raw in markdown.splitlines():
        heading = _command_help_heading(raw.strip())
        if heading is not None:
            text, level = heading
            if text.strip() == "输出":
                skipping = True
                skip_level = level
                continue
            if skipping and level <= skip_level:
                skipping = False
        if not skipping:
            kept.append(raw)
    return "\n".join(kept)


def _layout_command_help_markdown(markdown: str) -> tuple[str, list[_CommandHelpSection]]:
    markdown = _strip_command_help_output_section(_strip_command_help_frontmatter(markdown or ""))
    title = "指令帮助"
    sections: list[_CommandHelpSection] = []
    lines: list[_CommandHelpLine] = []
    section_title = "说明"
    in_code = False

    def flush_section() -> None:
        nonlocal lines
        while lines and not lines[0].text:
            lines.pop(0)
        while lines and not lines[-1].text:
            lines.pop()
        if lines:
            sections.append(_CommandHelpSection(section_title, lines))
        lines = []

    for raw in markdown.splitlines():
        trimmed_right = raw.rstrip("\r\t ")
        trimmed = trimmed_right.strip()
        if trimmed.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            _append_command_help_wrapped_line(
                lines,
                trimmed_right,
                font_name=DEFAULT_FONT,
                size=20,
                indent=26,
                fill=(42, 52, 68, 255),
                bg=(255, 255, 255, 116),
                gap_before=2,
            )
            continue
        if not trimmed:
            lines.append(_CommandHelpLine("", DEFAULT_FONT, 10, gap_before=10))
            continue
        if trimmed.startswith(("import ", "const ", "<")):
            continue

        heading = _command_help_heading(trimmed)
        if heading is not None:
            text, level = heading
            text = _clean_command_help_inline(text)
            if level == 1:
                title = text or title
                continue
            if level == 2:
                flush_section()
                section_title = text or "说明"
                continue
            _append_command_help_wrapped_line(
                lines,
                text,
                font_name=DEFAULT_BOLD_FONT,
                size=23,
                fill=(26, 38, 58, 255),
                gap_before=16,
            )
            continue

        bullet = _command_help_bullet(trimmed)
        if bullet is not None:
            cleaned = _clean_command_help_inline(bullet)
            if "：" in cleaned or ":" in cleaned:
                _append_command_help_definition_line(lines, cleaned)
                continue
            _append_command_help_wrapped_line(
                lines,
                cleaned,
                font_name=DEFAULT_FONT,
                size=21,
                indent=34,
                fill=(50, 61, 78, 255),
                gap_before=7,
            )
            continue

        numbered = _command_help_numbered(trimmed)
        if numbered is not None:
            _append_command_help_wrapped_line(
                lines,
                _clean_command_help_inline(numbered),
                font_name=DEFAULT_FONT,
                size=21,
                indent=36,
                fill=(50, 61, 78, 255),
                gap_before=7,
            )
            continue

        if trimmed.startswith(">"):
            _append_command_help_wrapped_line(
                lines,
                _clean_command_help_inline(trimmed.lstrip(">").strip()),
                font_name=DEFAULT_FONT,
                size=20,
                indent=28,
                fill=(87, 103, 126, 255),
                bg=(255, 255, 255, 104),
                gap_before=10,
            )
            continue

        if trimmed.startswith("|") and "|" in trimmed[1:]:
            _append_command_help_wrapped_line(
                lines,
                _clean_command_help_inline(trimmed),
                font_name=DEFAULT_FONT,
                size=18,
                indent=24,
                fill=(42, 52, 68, 255),
                bg=(255, 255, 255, 112),
                gap_before=6,
            )
            continue

        _append_command_help_wrapped_line(
            lines,
            _clean_command_help_inline(trimmed),
            font_name=DEFAULT_FONT,
            size=21,
            fill=(50, 61, 78, 255),
            gap_before=7,
        )

    flush_section()
    return title, sections


def _compose_command_help_image_sync(rqd: CommandHelpRenderRequest) -> Image.Image:
    title, sections = _layout_command_help_markdown(rqd.markdown)
    title = (rqd.title or title or "指令帮助").strip()
    if not sections:
        sections = [_CommandHelpSection("说明", [])]

    content_w = _HELP_IMAGE_WIDTH - _HELP_CARD_MARGIN * 2
    section_gap = 22
    section_pad_x = 26
    section_pad_y = 20
    title_h = 88
    height = _HELP_CARD_MARGIN + title_h + section_gap
    section_sizes: list[tuple[int, int]] = []
    for section in sections:
        section_h = section_pad_y * 2 + 42
        for line in section.lines:
            section_h += line.gap_before + _command_help_line_height(line.size)
        section_h = max(92, section_h)
        section_sizes.append((content_w, section_h))
        height += section_h + section_gap
    height = max(360, height + _HELP_CARD_MARGIN - section_gap)

    img = Image.new("RGBA", (_HELP_IMAGE_WIDTH, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img, "RGBA")

    def draw_glass_box(box: tuple[int, int, int, int], radius: int, fill_alpha: int = 112) -> None:
        shadow = Image.new("RGBA", img.size, (255, 255, 255, 0))
        shadow_draw = ImageDraw.Draw(shadow, "RGBA")
        shadow_draw.rounded_rectangle(
            (box[0] + 4, box[1] + 6, box[2] + 4, box[3] + 6),
            radius=radius,
            fill=(72, 96, 128, 30),
        )
        img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10)))
        draw.rounded_rectangle(
            box,
            radius=radius,
            fill=(255, 255, 255, fill_alpha),
            outline=(255, 255, 255, 150),
            width=2,
        )

    title_box = (
        _HELP_CARD_MARGIN,
        _HELP_CARD_MARGIN,
        _HELP_IMAGE_WIDTH - _HELP_CARD_MARGIN,
        _HELP_CARD_MARGIN + title_h,
    )
    draw_glass_box(title_box, 22, 118)
    draw.text(
        (_HELP_CARD_MARGIN + 30, _HELP_CARD_MARGIN + 24),
        title,
        font=get_font(DEFAULT_HEAVY_FONT, 34),
        fill=(24, 38, 58, 255),
    )

    y = title_box[3] + section_gap
    for section, (_, section_h) in zip(sections, section_sizes, strict=True):
        section_box = (_HELP_CARD_MARGIN, y, _HELP_IMAGE_WIDTH - _HELP_CARD_MARGIN, y + section_h)
        draw_glass_box(section_box, 18, 102)
        header_box = (section_box[0] + 24, section_box[1] + 18, section_box[2] - 24, section_box[1] + 50)
        draw.text(
            (header_box[0], header_box[1]),
            section.title,
            font=get_font(DEFAULT_BOLD_FONT, 24),
            fill=(24, 38, 58, 255),
        )
        draw.line(
            (header_box[0], header_box[3] + 8, header_box[2], header_box[3] + 8),
            fill=(255, 255, 255, 86),
            width=2,
        )

        text_y = section_box[1] + section_pad_y + 48
        text_x = section_box[0] + section_pad_x
        text_right = section_box[2] - section_pad_x
        for line in section.lines:
            text_y += line.gap_before
            line_height = _command_help_line_height(line.size)
            if line.bg is not None:
                bg_box = (
                    text_x + line.indent - 14,
                    text_y - 4,
                    text_right + 8,
                    text_y + line_height - 1,
                )
                draw.rounded_rectangle(bg_box, radius=10, fill=line.bg)
            if line.text:
                font = get_font(line.font_name, line.size)
                if line.label:
                    draw.text(
                        (text_x + line.indent, text_y),
                        line.label,
                        font=get_font(DEFAULT_BOLD_FONT, line.size),
                        fill=(30, 45, 66, 255),
                    )
                text_offset = line.label_width if line.label_width > 0 else 0
                draw.text((text_x + line.indent + text_offset, text_y), line.text, font=font, fill=line.fill)
            text_y += line_height
        y += section_h + section_gap

    return img


async def _build_command_help_canvas(rqd: CommandHelpRenderRequest) -> Canvas:
    panel = await run_in_pool(partial(_compose_command_help_image_sync, rqd))
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        Frame().set_size(panel.size).add_draw_func(
            lambda _widget, painter: painter.paste_with_alpha_blend(panel, (0, 0), exclude_on_hash=True)
        )
    add_request_watermark(canvas, rqd)
    return canvas


async def compose_command_help_image(rqd: CommandHelpRenderRequest) -> Image.Image:
    canvas = await _build_command_help_canvas(rqd)
    return await canvas.get_img()


async def try_render_command_help_payload(rqd: CommandHelpRenderRequest) -> EncodedImagePayload | None:
    """Skia 路径：帮助面板位图经 mem 图传输,外壳走 IRPainter;不可用时返回 None 回退 Pillow。"""
    if not skia_plot_enabled():
        return None
    canvas = await _build_command_help_canvas(rqd)
    # The /help route pins PNG output regardless of the global export format.
    return await render_canvas_payload(canvas, endpoint="command_help", export_format="png")


def _with_alpha(color: tuple[int, int, int], alpha: int) -> tuple[int, int, int, int]:
    return (color[0], color[1], color[2], alpha)


def _resolve_alias_accent(entity_label: str, entity_id: int) -> tuple[int, int, int]:
    if "角色" in entity_label:
        if color_code := CHARACTER_COLOR_CODE.get(entity_id):
            return tuple(color_code_to_rgb(color_code))
        return (255, 204, 170)
    return (110, 180, 255)


def _resolve_alias_name_box_width(
    name: str,
    has_jacket: bool,
    has_trim: bool = False,
    panel_w: int | None = None,
) -> int:
    display_len = max(1, get_str_display_length(name.strip()))
    estimated = 112 + display_len * 14
    if has_trim:
        max_w = panel_w - (214 if has_jacket else 146) if panel_w is not None else None
        if has_jacket:
            return max(220, min(max_w or 420, estimated))
        return max(260, min(max_w or 500, estimated + 24))
    if has_jacket:
        return max(240, min(648, estimated))
    return max(280, min(760, estimated))


def _resolve_alias_trim_path(rqd: AliasListRequest) -> str | None:
    if rqd.character_silhouette_path and rqd.character_silhouette_path.strip():
        return rqd.character_silhouette_path.strip()
    if rqd.character_trim_path and rqd.character_trim_path.strip():
        return rqd.character_trim_path.strip()
    return None


def _prepare_alias_trim_image(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    alpha_floor = _ALIAS_TRIM_ALPHA_FLOOR
    alpha = img.getchannel("A").point(
        lambda v: 0 if v <= alpha_floor else min(255, int((v - alpha_floor) * 255 / (255 - alpha_floor)))
    )
    img.putalpha(alpha)
    return img


async def _load_chara_birthday_assets(
    rqd: CharaBirthdayRequest,
) -> tuple[ImageSource, ImageSource, ImageSource, list[Image.Image], dict[int, Image.Image], float]:
    tasks = [
        # ImageBg keeps this lazy through the Skia path; Pillow resolves it during replay.
        get_asset_image_ref(ASSETS_BASE_DIR, rqd.card_image_path),
        get_asset_image_ref(ASSETS_BASE_DIR, rqd.sd_image_path),
        get_asset_image_ref(ASSETS_BASE_DIR, rqd.title_image_path),
        *[
            get_img_resized(
                ASSETS_BASE_DIR,
                card.thumbnail_path,
                _BIRTHDAY_CARD_THUMB_SIZE,
                _BIRTHDAY_CARD_THUMB_SIZE,
            )
            for card in rqd.cards
        ],
        *[
            get_img_resized(
                ASSETS_BASE_DIR,
                chara.icon_path,
                _BIRTHDAY_CALENDAR_ICON_SIZE,
                _BIRTHDAY_CALENDAR_ICON_SIZE,
            )
            for chara in rqd.all_characters
        ],
    ]
    started = time.perf_counter()
    results = await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - started

    card_count = len(rqd.cards)
    card_image, sd_image, title_image = results[0], results[1], results[2]
    card_thumbs = list(results[3 : 3 + card_count])
    calendar_icons = {
        chara.cid: icon
        for chara, icon in zip(
            rqd.all_characters,
            results[3 + card_count :],
            strict=False,
        )
    }
    return card_image, sd_image, title_image, card_thumbs, calendar_icons, elapsed


def _resolve_alias_trim_metrics(
    trim_img: Image.Image, left_panel_w: int, left_panel_h: int
) -> tuple[int, int, int, tuple[int, int]]:
    trim_display_h = max(500, min(920, left_panel_h + _ALIAS_TRIM_BOTTOM_OVERFLOW))
    aspect_ratio = trim_img.width / max(1, trim_img.height)
    max_allowed_overlap = max(_ALIAS_TRIM_MIN_OVERLAP, min(_ALIAS_TRIM_MAX_OVERLAP, int(left_panel_w * 0.18)))
    max_rendered_w = _ALIAS_TRIM_MAX_FRAME_W + max_allowed_overlap
    rendered_w = max(1, int(trim_display_h * aspect_ratio))

    if rendered_w > max_rendered_w:
        trim_display_h = max(_ALIAS_TRIM_MIN_DISPLAY_H, int(max_rendered_w / max(aspect_ratio, 1e-6)))
        rendered_w = max(1, int(trim_display_h * aspect_ratio))

    desired_overlap = max(
        _ALIAS_TRIM_MIN_OVERLAP,
        min(
            max_allowed_overlap,
            int(rendered_w * 0.10) + max(0, int((aspect_ratio - 1.0) * 28)),
        ),
    )
    trim_frame_w = max(_ALIAS_TRIM_MIN_FRAME_W, min(_ALIAS_TRIM_MAX_FRAME_W, rendered_w - desired_overlap))
    trim_frame_w = min(trim_frame_w, rendered_w)
    trim_frame_h = left_panel_h
    trim_offset = (0, _ALIAS_TRIM_BOTTOM_OVERFLOW)
    return trim_frame_w, trim_frame_h, trim_display_h, trim_offset


def _build_alias_info_panel(
    rqd: AliasListRequest,
    accent: tuple[int, int, int],
    jacket_img: Image.Image | None,
    aliases_count: int,
    panel_w: int,
    name_box_w: int,
    style_name: TextStyle,
    style_meta: TextStyle,
    style_id: TextStyle,
    style_badge: TextStyle,
):
    info_panel = (
        HSplit()
        .set_content_align("lt")
        .set_item_align("t")
        .set_sep(16)
        .set_padding(18)
        .set_bg(roundrect_bg(alpha=86, blur_glass_kwargs={"blur": 8}))
    )

    id_block = (
        VSplit()
        .set_content_align("c")
        .set_item_align("c")
        .set_sep(8)
        .set_padding((18, 16))
        .set_bg(RoundRectBg(fill=_with_alpha(accent, 205), radius=14))
    )
    id_block.add_item(TextBox(rqd.entity_label, TextStyle(DEFAULT_BOLD_FONT, 18, WHITE)))
    id_block.add_item(TextBox(str(rqd.entity_id), style_id))

    detail_row = HSplit().set_content_align("lt").set_item_align("t").set_sep(16)
    text_col = VSplit().set_content_align("l").set_item_align("l").set_sep(10)
    text_col.add_item(TextBox(rqd.entity_name, style_name, use_real_line_count=True).set_w(name_box_w))
    text_col.add_item(TextBox("下列结果为已审核通过的别名展示", style_meta))
    badge_row = HSplit().set_content_align("l").set_item_align("c").set_sep(10)
    badge_row.add_item(
        TextBox(f"已审核别名 {aliases_count} 条", style_badge)
        .set_padding((14, 8))
        .set_bg(
            RoundRectBg(
                fill=(255, 255, 255, 128),
                radius=10,
                stroke=_with_alpha(accent, 96),
                stroke_width=1,
            )
        )
    )
    badge_row.add_item(
        TextBox("过多时自动转为图片返回", style_meta)
        .set_padding((14, 8))
        .set_bg(
            RoundRectBg(
                fill=(255, 255, 255, 128),
                radius=10,
                stroke=_with_alpha(accent, 96),
                stroke_width=1,
            )
        )
    )
    text_col.add_item(badge_row)
    detail_row.add_item(text_col)

    if jacket_img is not None:
        detail_row.add_item(
            ImageBox(jacket_img, size=(92, 92), use_alpha_blend=True, shadow=True)
            .set_bg(
                RoundRectBg(
                    fill=(255, 255, 255, 128),
                    radius=12,
                    stroke=_with_alpha(accent, 96),
                    stroke_width=1,
                )
            )
            .set_padding(4)
        )

    info_panel.add_item(id_block)
    info_panel.add_item(detail_row)
    return info_panel


def _build_alias_list_panel(
    aliases: list[str],
    accent: tuple[int, int, int],
    panel_w: int,
    flow_w: int,
    style_label: TextStyle,
    style_badge: TextStyle,
    style_chip: TextStyle,
):
    alias_panel = (
        VSplit()
        .set_w(panel_w)
        .set_content_align("lt")
        .set_item_align("lt")
        .set_sep(12)
        .set_padding(18)
        .set_bg(roundrect_bg(alpha=84, blur_glass_kwargs={"blur": 8}))
    )
    header_row = HSplit().set_content_align("l").set_item_align("c").set_sep(12)
    header_row.add_item(TextBox("已审核别名", style_label))
    header_row.add_item(
        TextBox(f"{len(aliases)}", style_badge)
        .set_padding((12, 6))
        .set_bg(
            RoundRectBg(
                fill=(255, 255, 255, 128),
                radius=9,
                stroke=_with_alpha(accent, 96),
                stroke_width=1,
            )
        )
    )
    alias_panel.add_item(header_row)

    flow = Flow().set_w(flow_w).set_content_align("lt").set_item_align("lt").set_sep(10, 10)
    for alias in aliases:
        flow.add_item(
            TextBox(alias, style_chip)
            .set_bg(
                RoundRectBg(
                    fill=(255, 255, 255, 136),
                    radius=11,
                    stroke=_with_alpha(accent, 108),
                    stroke_width=1,
                )
            )
            .set_padding((14, 9))
        )
    alias_panel.add_item(flow)
    return alias_panel


def _build_alias_left_panel(
    rqd: AliasListRequest,
    aliases: list[str],
    accent: tuple[int, int, int],
    jacket_img: Image.Image | None,
    panel_w: int,
    flow_w: int,
    name_box_w: int,
    style_name: TextStyle,
    style_meta: TextStyle,
    style_id: TextStyle,
    style_badge: TextStyle,
    style_label: TextStyle,
    style_chip: TextStyle,
) -> VSplit:
    token = Widget._thread_local.set(None)
    try:
        left_panel = VSplit().set_w(panel_w).set_content_align("lt").set_item_align("lt").set_sep(16)
        left_panel.add_item(
            _build_alias_info_panel(
                rqd,
                accent,
                jacket_img,
                len(aliases),
                panel_w,
                name_box_w,
                style_name,
                style_meta,
                style_id,
                style_badge,
            )
        )
        left_panel.add_item(
            _build_alias_list_panel(
                aliases,
                accent,
                panel_w,
                flow_w,
                style_label,
                style_badge,
                style_chip,
            )
        )
        return left_panel
    finally:
        Widget._thread_local.reset(token)


def _build_alias_trim_panel(trim_img: Image.Image, left_panel_size: tuple[int, int]) -> Frame:
    token = Widget._thread_local.set(None)
    try:
        left_panel_w, left_panel_h = left_panel_size
        trim_frame_w, trim_frame_h, trim_display_h, trim_offset = _resolve_alias_trim_metrics(
            trim_img, left_panel_w, left_panel_h
        )
        trim_panel = Frame().set_size((trim_frame_w, trim_frame_h)).set_content_align("rb").set_allow_draw_outside(True)
        trim_panel.add_item(
            ImageBox(trim_img, size=(None, trim_display_h), use_alpha_blend=True).set_offset(trim_offset)
        )
        return trim_panel
    finally:
        Widget._thread_local.reset(token)


def _resolve_alias_panel_widths(
    rqd: AliasListRequest,
    aliases: list[str],
    accent: tuple[int, int, int],
    jacket_img: Image.Image | None,
    target_h: int,
    style_name: TextStyle,
    style_meta: TextStyle,
    style_id: TextStyle,
    style_badge: TextStyle,
    style_label: TextStyle,
    style_chip: TextStyle,
) -> tuple[int, int, int]:
    candidate_widths = [700, 780, 860, 940, 1020, 1100]
    best_fit: tuple[int, int, int] | None = None
    best_overflow: tuple[int, int, int, int] | None = None

    for panel_w in candidate_widths:
        flow_w = panel_w - 80
        name_box_w = _resolve_alias_name_box_width(rqd.entity_name, jacket_img is not None, True, panel_w)
        temp_left_panel = _build_alias_left_panel(
            rqd,
            aliases,
            accent,
            jacket_img,
            panel_w,
            flow_w,
            name_box_w,
            style_name,
            style_meta,
            style_id,
            style_badge,
            style_label,
            style_chip,
        )
        left_h = temp_left_panel._get_self_size()[1]
        if left_h <= target_h:
            best_fit = (panel_w, flow_w, name_box_w)
            break
        overflow = left_h - target_h
        if best_overflow is None or overflow < best_overflow[0]:
            best_overflow = (overflow, panel_w, flow_w, name_box_w)

    if best_fit is not None:
        return best_fit
    assert best_overflow is not None
    return best_overflow[1], best_overflow[2], best_overflow[3]


async def _build_chara_birthday_canvas(rqd: CharaBirthdayRequest) -> Canvas:
    r"""_build_chara_birthday_canvas

    合成角色生日图片

    Args
    ----
    rqd : CharaBirthdayRequest
        绘制角色生日图片所必须的数据

    Returns
    -------
    Canvas
    """
    cid = rqd.cid
    month = rqd.month
    day = rqd.day
    region_name = rqd.region_name
    days_until_birthday = rqd.days_until_birthday
    color_code = rqd.color_code
    cards = rqd.cards
    all_characters = rqd.all_characters

    is_fifth_anniv = rqd.is_fifth_anniv

    style1 = TextStyle(DEFAULT_BOLD_FONT, 24, BLACK)
    style2 = TextStyle(DEFAULT_FONT, 20, BLACK)

    card_image, sd_image, title_image, card_thumbs, calendar_icons, _ = await _load_chara_birthday_assets(rqd)

    # 绘制时间范围的辅助函数
    def draw_time_range(label: str, tr: BirthdayEventTime):
        start_at = datetime_from_millis(tr.start_at, rqd.timezone)
        end_at = datetime_from_millis(tr.end_at, rqd.timezone)
        timezone_label = rqd.timezone or ""
        if timezone_label == "" and (start_at and start_at.tzinfo):
            timezone_label = start_at.tzname() or ""
        if timezone_label == "" and (end_at and end_at.tzinfo):
            timezone_label = end_at.tzname() or ""
        if timezone_label:
            timezone_label = f" ({timezone_label})"
        with HSplit().set_sep(8).set_content_align("l").set_item_align("l"):
            TextBox(f"{label} ", style1)
            TextBox(
                (f"{start_at.strftime('%m-%d %H:%M')} ~ {end_at.strftime('%m-%d %H:%M')}{timezone_label}"),
                style2,
            )

    with Canvas(bg=ImageBg(card_image)).set_padding(BG_PADDING) as canvas:
        with (
            VSplit()
            .set_content_align("c")
            .set_item_align("c")
            .set_padding(16)
            .set_sep(8)
            .set_item_bg(roundrect_bg(alpha=80))
            .set_bg(roundrect_bg(alpha=80))
        ):
            # 角色信息头部
            with HSplit().set_sep(16).set_padding(16).set_content_align("c").set_item_align("c"):
                ImageBox(sd_image, size=(None, 80), shadow=True)
                ImageBox(title_image, size=(None, 60))
                TextBox(
                    f"{month}月{day}日",
                    TextStyle(
                        DEFAULT_HEAVY_FONT,
                        32,
                        (100, 100, 100),
                        use_shadow=True,
                        shadow_offset=2,
                        shadow_color=tuple(color_code_to_rgb(color_code)),
                    ),
                )

            # 基本信息
            with VSplit().set_sep(4).set_padding(16).set_content_align("l").set_item_align("l"):
                with HSplit().set_sep(8).set_padding(0).set_content_align("l").set_item_align("l"):
                    TextBox(f"({region_name}) 距离下次生日还有{days_until_birthday}天", style1)
                    Spacer(w=16)
                    TextBox("应援色", style1)
                    TextBox(color_code, TextStyle(DEFAULT_FONT, 20, ADAPTIVE_WB)).set_bg(
                        RoundRectBg(tuple(color_code_to_rgb(color_code)), radius=4)
                    ).set_padding(8)

                # 时间范围 - 固定绘制
                draw_time_range("🎰卡池开放时间", rqd.gacha_time)
                draw_time_range("🎤虚拟LIVE时间", rqd.live_time)

            # 五周年特殊时间范围
            if is_fifth_anniv:
                with VSplit().set_sep(4).set_padding(16).set_content_align("l").set_item_align("l"):
                    if rqd.drop_time:
                        draw_time_range("💧露滴掉落时间", rqd.drop_time)
                    if rqd.flower_time:
                        draw_time_range("🌱浇水开放时间", rqd.flower_time)
                    if rqd.party_time:
                        draw_time_range("🎂派对开放时间", rqd.party_time)

            # 卡牌列表
            with HSplit().set_sep(4).set_padding(16).set_content_align("l").set_item_align("l"):
                TextBox("卡牌", style1)
                Spacer(w=8)
                with Grid(col_count=6).set_sep(4, 4):
                    for i, thumb in enumerate(card_thumbs):
                        with VSplit().set_sep(2).set_content_align("c").set_item_align("c"):
                            ImageBox(thumb, size=(80, 80), shadow=True)
                            TextBox(f"{cards[i].id}", TextStyle(DEFAULT_FONT, 16, (50, 50, 50)))

            # 底部角色生日日历
            with Grid(col_count=13).set_sep(2, 2).set_padding(16).set_content_align("c").set_item_align("c"):
                # 找到起始角色（从小豆沙开始，ID=6）
                idx = 0
                start_cid = 6
                for i, item in enumerate(all_characters):
                    if item.cid == start_cid:
                        idx = i
                        break

                for _ in range(len(all_characters)):
                    chara = all_characters[idx % len(all_characters)]
                    idx += 1

                    with VSplit().set_sep(0).set_content_align("c").set_item_align("c"):
                        # 使用model中传入的icon_path
                        chara_icon = calendar_icons[chara.cid]

                        b = ImageBox(chara_icon, size=(40, 40)).set_padding(4)
                        if chara.cid == cid:
                            b.set_bg(roundrect_bg(radius=8, alpha=80))
                        TextBox(f"{chara.month}/{chara.day}", TextStyle(DEFAULT_FONT, 14, (50, 50, 80)))

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_chara_birthday_image(rqd: CharaBirthdayRequest) -> Image.Image:
    return await (await _build_chara_birthday_canvas(rqd)).get_img()


async def try_render_chara_birthday_payload(rqd: CharaBirthdayRequest) -> EncodedImagePayload | None:
    # Renders inside a heavy-worker process; the parent replays this outcome from the payload's
    # backend under the pool kind "chara_birthday", so the endpoint name must match that kind.
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_chara_birthday_canvas(rqd), endpoint="chara_birthday")


async def _build_alias_list_canvas(rqd: AliasListRequest) -> Canvas:
    aliases = [alias.strip() for alias in rqd.aliases if alias and alias.strip()]
    accent = _resolve_alias_accent(rqd.entity_label, rqd.entity_id)
    jacket_img = None
    if rqd.music_jacket_path:
        jacket_img = await get_img_resized(ASSETS_BASE_DIR, rqd.music_jacket_path, 92, 92)
    trim_img = None
    trim_path = _resolve_alias_trim_path(rqd)
    if trim_path:
        try:
            trim_img = _prepare_alias_trim_image(
                await get_img_from_path(ASSETS_BASE_DIR, trim_path, on_missing="raise")
            )
        except (FileNotFoundError, OSError, ValueError):
            trim_img = None

    style_title = TextStyle(
        DEFAULT_HEAVY_FONT,
        30,
        BLACK,
        use_shadow=True,
        shadow_offset=2,
        shadow_color=_with_alpha(accent, 180),
    )
    style_name = TextStyle(DEFAULT_HEAVY_FONT, 28, BLACK)
    style_label = TextStyle(DEFAULT_BOLD_FONT, 20, (60, 60, 80))
    style_meta = TextStyle(DEFAULT_FONT, 17, (78, 78, 98))
    style_id = TextStyle(DEFAULT_HEAVY_FONT, 34, WHITE)
    style_badge = TextStyle(DEFAULT_BOLD_FONT, 18, (66, 66, 86))
    style_chip = TextStyle(DEFAULT_FONT, 18, (48, 48, 64))

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            TextBox(rqd.title, style_title).set_padding((20, 16)).set_bg(
                roundrect_bg(alpha=88, blur_glass_kwargs={"blur": 8})
            )

            if trim_img is not None:
                panel_w, flow_w, name_box_w = _resolve_alias_panel_widths(
                    rqd,
                    aliases,
                    accent,
                    jacket_img,
                    760,
                    style_name,
                    style_meta,
                    style_id,
                    style_badge,
                    style_label,
                    style_chip,
                )
                left_panel = _build_alias_left_panel(
                    rqd,
                    aliases,
                    accent,
                    jacket_img,
                    panel_w,
                    flow_w,
                    name_box_w,
                    style_name,
                    style_meta,
                    style_id,
                    style_badge,
                    style_label,
                    style_chip,
                )
                trim_panel = _build_alias_trim_panel(trim_img, left_panel._get_self_size())
                HSplit().set_content_align("lt").set_item_align("t").set_sep(0).add_item(left_panel).add_item(
                    trim_panel
                )
            else:
                name_box_w = _resolve_alias_name_box_width(rqd.entity_name, jacket_img is not None)
                with (
                    HSplit()
                    .set_content_align("t")
                    .set_item_align("t")
                    .set_sep(16)
                    .set_padding(18)
                    .set_bg(roundrect_bg(alpha=86, blur_glass_kwargs={"blur": 8}))
                ):
                    with (
                        VSplit()
                        .set_content_align("c")
                        .set_item_align("c")
                        .set_sep(8)
                        .set_padding((18, 16))
                        .set_bg(RoundRectBg(fill=_with_alpha(accent, 205), radius=14))
                    ):
                        TextBox(rqd.entity_label, TextStyle(DEFAULT_BOLD_FONT, 18, WHITE))
                        TextBox(str(rqd.entity_id), style_id)

                    with HSplit().set_content_align("t").set_item_align("t").set_sep(16):
                        with VSplit().set_content_align("l").set_item_align("l").set_sep(10):
                            TextBox(rqd.entity_name, style_name, use_real_line_count=True).set_w(name_box_w)
                            TextBox("下列结果为已审核通过的别名展示", style_meta)
                            with HSplit().set_content_align("c").set_item_align("c").set_sep(10):
                                TextBox(f"已审核别名 {len(aliases)} 条", style_badge).set_padding((14, 8)).set_bg(
                                    RoundRectBg(
                                        fill=(255, 255, 255, 128),
                                        radius=10,
                                        stroke=_with_alpha(accent, 96),
                                        stroke_width=1,
                                    )
                                )
                                TextBox("过多时自动转为图片返回", style_meta).set_padding((14, 8)).set_bg(
                                    RoundRectBg(
                                        fill=(255, 255, 255, 128),
                                        radius=10,
                                        stroke=_with_alpha(accent, 96),
                                        stroke_width=1,
                                    )
                                )
                        if jacket_img is not None:
                            ImageBox(jacket_img, size=(92, 92), use_alpha_blend=True, shadow=True).set_bg(
                                RoundRectBg(
                                    fill=(255, 255, 255, 128),
                                    radius=12,
                                    stroke=_with_alpha(accent, 96),
                                    stroke_width=1,
                                )
                            ).set_padding(4)

                with (
                    VSplit()
                    .set_content_align("lt")
                    .set_item_align("lt")
                    .set_sep(12)
                    .set_padding(18)
                    .set_bg(roundrect_bg(alpha=84, blur_glass_kwargs={"blur": 8}))
                ):
                    with HSplit().set_content_align("c").set_item_align("c").set_sep(12):
                        TextBox("已审核别名", style_label)
                        TextBox(f"{len(aliases)}", style_badge).set_padding((12, 6)).set_bg(
                            RoundRectBg(
                                fill=(255, 255, 255, 128),
                                radius=9,
                                stroke=_with_alpha(accent, 96),
                                stroke_width=1,
                            )
                        )
                    with Flow().set_w(980).set_content_align("lt").set_item_align("lt").set_sep(10, 10):
                        for alias in aliases:
                            TextBox(alias, style_chip).set_bg(
                                RoundRectBg(
                                    fill=(255, 255, 255, 136),
                                    radius=11,
                                    stroke=_with_alpha(accent, 108),
                                    stroke_width=1,
                                )
                            ).set_padding((14, 9))

    add_request_watermark(canvas, rqd)
    return canvas


# alias-list 的结果缓存(内存 + 磁盘 + Skia payload)已全部删除。
#
# 这张图上有 `add_request_watermark`,水印会把每请求的 `dt` 渲染成秒级 `DT: yyyy-mm-dd HH:MM:SS`;
# 而这里的 cache key 不含 dt ⇒ 一旦命中就会发**上一次请求的时间戳**(磁盘那层还跨重启存活)。
# 调用方 Haruki-Cloud 正是为了这个原因**专门让 alias-list 绕过它自己的渲染缓存**
# (internal/pjsk/drawing/client.go:361 "Alias-list watermarks include request DT, so we
# intentionally bypass the render cache here to avoid serving stale timestamps."),
# drawing 侧再缓存一次就把 cloud 的意图整个抵消掉了。
#
# 其它端点 cloud 是接受陈旧水印的(默认 24h TTL,card/list 等甚至永不过期),且它的 key 与这里等价
# ——cloud 命中就不会调 drawing,cloud 未命中这里也不会命中,所以 drawing 侧的结果缓存本就是死重。
async def compose_alias_list_image(rqd: AliasListRequest) -> Image.Image:
    """合成别名列表图片 (Pillow 路径)。"""
    return await (await _build_alias_list_canvas(rqd)).get_img()


async def try_render_alias_list_payload(rqd: AliasListRequest) -> EncodedImagePayload | None:
    """Skia 路径:经 IRPainter 渲染同一棵 widget 树;不可用时返回 None 回退 Pillow。"""
    if not skia_plot_enabled():
        return None
    canvas = await _build_alias_list_canvas(rqd)
    return await render_canvas_payload(canvas, endpoint="alias_list")
