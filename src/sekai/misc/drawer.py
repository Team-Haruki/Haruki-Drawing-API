from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.chrome import (
    AMBER,
    CHIP_STYLE,
    DIM,
    INK,
    PANEL_PAD,
    RED,
    SLATE,
    SUBTITLE_STYLE,
    TEXT,
    TITLE_STYLE,
    Color,
    alpha as chrome_alpha,
    chip,
    fit_style,
    ink,
    mix,
    panel,
    section_header,
    soft_chip,
    text_w,
)
from src.sekai.base.draw import (
    BG_PADDING,
    CHARACTER_COLOR_CODE,
    SEKAI_BLUE_BG,
    Canvas,
    TextBox,
    add_request_watermark,
)
from src.sekai.base.font_metrics import get_layout_font as get_font
from src.sekai.base.image_info import probe_alpha_bounds
from src.sekai.base.image_source import MissingImageRef
from src.sekai.base.paint_types import ADAPTIVE_WB, WHITE, color_code_to_rgb, get_font_desc
from src.sekai.base.plot import (
    AlphaTrimImageBox,
    CanvasImageBox,
    Flow,
    Frame,
    HSplit,
    ImageBg,
    ImageBox,
    RoundClipFrame,
    RoundRectBg,
    Spacer,
    TextStyle,
    VSplit,
    Widget,
)
from src.sekai.base.text_layout import ascender_top_to_painter_y, get_text_size
from src.sekai.base.timezone import datetime_from_millis
from src.sekai.base.utils import (
    ImageSource,
    get_asset_image_ref,
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
from .model import (
    AliasListRequest,
    BirthdayEventTime,
    CharaBirthdayData,
    CharaBirthdayRequest,
    CommandHelpRenderRequest,
)

logger = logging.getLogger(__name__)
_birthday_perf_logger = logging.getLogger("misc.birthday.perf")

# =========================== 颜色常量 =========================== #

BLACK = (0, 0, 0, 255)
_HELP_IMAGE_WIDTH = 1080
_HELP_MARGIN = 62
_HELP_CARD_MARGIN = 28
_HELP_MAX_TEXT_WIDTH = _HELP_IMAGE_WIDTH - _HELP_MARGIN * 2
_HELP_LINK_RE = re.compile(r"\[([^\]]+)]\([^)]+\)")


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
    stripped = line.strip()
    level = len(stripped) - len(stripped.lstrip("#"))
    if not 1 <= level <= 6 or len(stripped) <= level or not stripped[level].isspace():
        return None
    heading = stripped[level:].strip()
    return (heading, level) if heading else None


def _command_help_bullet(line: str) -> str | None:
    stripped = line.strip()
    if len(stripped) < 3 or stripped[0] not in "-*+" or not stripped[1].isspace():
        return None
    bullet = stripped[2:].strip()
    return bullet or None


def _command_help_numbered(line: str) -> str | None:
    stripped = line.strip()
    digit_count = len(stripped) - len(stripped.lstrip("0123456789"))
    marker_end = digit_count + 1
    if (
        digit_count == 0
        or len(stripped) <= marker_end
        or stripped[digit_count] not in ".)"
        or not stripped[marker_end].isspace()
    ):
        return None
    item = stripped[marker_end:].strip()
    return f"{stripped[:marker_end]} {item}" if item else None


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

    for raw in markdown.splitlines():
        trimmed_right = raw.rstrip("\r\t ")
        trimmed = trimmed_right.strip()
        if trimmed.startswith("```"):
            in_code = not in_code
            continue
        heading = _command_help_heading(trimmed)
        if heading is not None and not in_code:
            text, level = heading
            text = _clean_command_help_inline(text)
            if level == 1:
                title = text or title
                continue
            if level == 2:
                _flush_command_help_section(sections, section_title, lines)
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
        _append_command_help_body_line(lines, trimmed_right, trimmed, in_code=in_code)

    _flush_command_help_section(sections, section_title, lines)
    return title, sections


def _build_command_help_panel(rqd: CommandHelpRenderRequest) -> Canvas:
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

    panel = Canvas(w=_HELP_IMAGE_WIDTH, h=height).set_padding(0)

    def draw(_widget, painter):
        def draw_roundrect(box, radius, fill, outline=None, width=1):
            painter.roundrect_src(
                (box[0], box[1]),
                (box[2] - box[0] + 1, box[3] - box[1] + 1),
                fill,
                radius,
                stroke=outline,
                stroke_width=width,
            )

        def draw_glass_box(box, radius, fill_alpha=112):
            painter.drop_shadow_roundrect(
                (box[0], box[1]),
                (box[2] - box[0] + 1, box[3] - box[1] + 1),
                radius,
                (72, 96, 128, 30),
                sigma=10,
                offset=(4, 6),
            )
            draw_roundrect(box, radius, (255, 255, 255, fill_alpha), (255, 255, 255, 150), 2)

        def draw_text(pos, text, font, fill):
            painter.text(
                text, (pos[0], ascender_top_to_painter_y(font.path, font.size, pos[1])), font, fill, mask_lerp=True
            )

        def draw_line(box, fill, width):
            painter.roundrect_src((box[0], box[1]), (box[2] - box[0] + 1, width), fill, 0)

        title_box = (
            _HELP_CARD_MARGIN,
            _HELP_CARD_MARGIN,
            _HELP_IMAGE_WIDTH - _HELP_CARD_MARGIN,
            _HELP_CARD_MARGIN + title_h,
        )
        draw_glass_box(title_box, 22, 118)
        draw_text(
            (_HELP_CARD_MARGIN + 30, _HELP_CARD_MARGIN + 24),
            title,
            font=get_font_desc(DEFAULT_HEAVY_FONT, 34),
            fill=(24, 38, 58, 255),
        )

        y = title_box[3] + section_gap
        for section, (_, section_h) in zip(sections, section_sizes, strict=True):
            section_box = (_HELP_CARD_MARGIN, y, _HELP_IMAGE_WIDTH - _HELP_CARD_MARGIN, y + section_h)
            draw_glass_box(section_box, 18, 102)
            header_box = (section_box[0] + 24, section_box[1] + 18, section_box[2] - 24, section_box[1] + 50)
            draw_text(
                (header_box[0], header_box[1]),
                section.title,
                font=get_font_desc(DEFAULT_BOLD_FONT, 24),
                fill=(24, 38, 58, 255),
            )
            draw_line(
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
                    draw_roundrect(bg_box, radius=10, fill=line.bg)
                if line.text:
                    font = get_font_desc(line.font_name, line.size)
                    if line.label:
                        draw_text(
                            (text_x + line.indent, text_y),
                            line.label,
                            font=get_font_desc(DEFAULT_BOLD_FONT, line.size),
                            fill=(30, 45, 66, 255),
                        )
                    text_offset = line.label_width if line.label_width > 0 else 0
                    draw_text((text_x + line.indent + text_offset, text_y), line.text, font=font, fill=line.fill)
                text_y += line_height
            y += section_h + section_gap

    panel.add_draw_func(draw)
    return panel


def _compose_command_help_image_sync(rqd: CommandHelpRenderRequest) -> Image.Image:
    return _build_command_help_panel(rqd).get_img_sync()


async def _build_command_help_canvas(rqd: CommandHelpRenderRequest) -> Canvas:
    panel = await run_in_pool(_build_command_help_panel, rqd)
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        CanvasImageBox(panel, use_alpha_blend=True)
    add_request_watermark(canvas, rqd)
    return canvas


async def compose_command_help_image(rqd: CommandHelpRenderRequest) -> Image.Image:
    canvas = await _build_command_help_canvas(rqd)
    return await canvas.get_img()


async def try_render_command_help_payload(rqd: CommandHelpRenderRequest) -> EncodedImagePayload | None:
    """Render the shared help panel subtree natively; retain the normal fail-open path."""
    if not skia_plot_enabled():
        return None
    canvas = await _build_command_help_canvas(rqd)
    # The /help route pins PNG output regardless of the global export format.
    return await render_canvas_payload(canvas, endpoint="command_help", export_format="png")


# =========================== 角色生日 / 别名列表 =========================== #

_PAGE_PAD = 16  # inner page padding on top of BG_PADDING, as on the deck pages
_PAGE_SEP = 14
_WELL_FILL = (255, 255, 255, 200)
_TITLE_CHIP_STYLE = CHIP_STYLE.replace(size=15)
_ADAPTIVE_CHIP_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=15, color=ADAPTIVE_WB)


def _with_alpha(color, alpha: int) -> Color:
    return chrome_alpha(color, alpha)


def _deep(accent) -> Color:
    """``accent`` darkened enough to letter with: pale cheer colours (#ffee11) are unreadable as text."""
    return mix(accent, INK, 0.45)


# --------------------------- 角色生日 --------------------------- #

_BIRTHDAY_MIN_W = 440  # the page is as wide as its header / event rows / card row need, within these bounds
_BIRTHDAY_MAX_W = 900
_BIRTHDAY_CARDS_PER_ROW = 5
_BIRTHDAY_CARD_SEP = 12
_BIRTHDAY_PANEL_ALPHA = 150  # panels sit on the card art, so they are more opaque than on the triangle background
_BIRTHDAY_SD_WELL = 72
_BIRTHDAY_SD = 64
_BIRTHDAY_LABEL_H = 40
_BIRTHDAY_CARD_THUMB_SIZE = 96
_BIRTHDAY_CALENDAR_ICON_SIZE = 40
_BIRTHDAY_EVENT_COLORS: dict[str, Color] = {
    "gacha": (146, 84, 222, 255),
    "live": (64, 132, 226, 255),
    "drop": (30, 168, 190, 255),
    "flower": (44, 170, 110, 255),
    "party": (228, 84, 110, 255),
}
_BIRTHDAY_EVENT_LABEL_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=15, color=WHITE)
_BIRTHDAY_SPAN_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=INK)
_BIRTHDAY_ID_STYLE = TextStyle(font=DEFAULT_FONT, size=13, color=DIM)
_BIRTHDAY_DATE_STYLE = TextStyle(font=DEFAULT_FONT, size=12, color=DIM)


@dataclass(frozen=True)
class _BirthdayEventRow:
    key: str
    label: str
    time: BirthdayEventTime


def _birthday_event_rows(rqd: CharaBirthdayRequest) -> list[_BirthdayEventRow]:
    rows = [
        _BirthdayEventRow("gacha", "卡池开放", rqd.gacha_time),
        _BirthdayEventRow("live", "虚拟LIVE", rqd.live_time),
    ]
    if rqd.is_fifth_anniv:
        for key, label, span in (
            ("drop", "露滴掉落", rqd.drop_time),
            ("flower", "浇水开放", rqd.flower_time),
            ("party", "派对开放", rqd.party_time),
        ):
            if span is not None:
                rows.append(_BirthdayEventRow(key, label, span))
    return rows


def _birthday_accent(color_code: str) -> Color:
    try:
        return tuple(color_code_to_rgb(color_code))
    except ValueError:
        return SLATE


def _birthday_countdown(days: int) -> tuple[str, Color]:
    if days <= 0:
        return "今天生日", RED
    if days == 1:
        return "明天生日", RED
    return f"还有 {days} 天", AMBER if days <= 7 else SLATE


def _birthday_timezone_label(start_at, end_at, timezone: str | None) -> str:
    timezone_label = timezone or ""
    if not timezone_label and start_at and start_at.tzinfo:
        timezone_label = start_at.tzname() or ""
    if not timezone_label and end_at and end_at.tzinfo:
        timezone_label = end_at.tzname() or ""
    return f" ({timezone_label})" if timezone_label else ""


def _birthday_span_text(start, end) -> str:
    return f"{start.strftime('%m-%d %H:%M')} ~ {end.strftime('%m-%d %H:%M')}"


def _birthday_span_length(start, end) -> str:
    """``7 天`` / ``36 小时``: the display end is one minute before the real end, so round up to it."""
    minutes = max(0, round((end - start).total_seconds() / 60)) + 1
    if minutes >= 24 * 60:
        return f"{round(minutes / (24 * 60))} 天"
    return f"{max(1, round(minutes / 60))} 小时"


def _birthday_calendar_cells(all_characters: list[CharaBirthdayData]) -> list[list[CharaBirthdayData]]:
    """Characters sharing a date (Rin and Len) form one cell; the order is the caller's (next birthday first)."""
    cells: dict[tuple[int, int], list[CharaBirthdayData]] = {}
    for chara in all_characters:
        cells.setdefault((chara.month, chara.day), []).append(chara)
    return list(cells.values())


async def _load_chara_birthday_assets(
    rqd: CharaBirthdayRequest,
) -> tuple[ImageSource, ImageSource, ImageSource, list[ImageSource], dict[int, ImageSource], float]:
    tasks = [
        get_asset_image_ref(ASSETS_BASE_DIR, rqd.card_image_path),
        get_asset_image_ref(ASSETS_BASE_DIR, rqd.sd_image_path),
        get_asset_image_ref(ASSETS_BASE_DIR, rqd.title_image_path),
        *[get_asset_image_ref(ASSETS_BASE_DIR, card.thumbnail_path) for card in rqd.cards],
        *[get_asset_image_ref(ASSETS_BASE_DIR, chara.icon_path) for chara in rqd.all_characters],
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


def _birthday_header_chips(rqd: CharaBirthdayRequest, accent: Color) -> list[tuple[str, Color, TextStyle]]:
    countdown, countdown_fill = _birthday_countdown(rqd.days_until_birthday)
    return [
        (rqd.region_name, SLATE, _TITLE_CHIP_STYLE),
        (f"{rqd.month}月{rqd.day}日", accent, _ADAPTIVE_CHIP_STYLE),
        (countdown, countdown_fill, _TITLE_CHIP_STYLE),
    ]


def _birthday_page_width(rqd: CharaBirthdayRequest, title_image, accent: Color) -> int:
    """The content width the header, the event rows and a row of card thumbnails need: most users read the
    page on a portrait phone, so the calendar wraps into more rows instead of setting the page width."""
    chips_w = sum(text_w(style, text) + 4 + 16 + 10 for text, _, style in _birthday_header_chips(rqd, accent))
    label_w = round(title_image.size[0] * _BIRTHDAY_LABEL_H / max(1, title_image.size[1]))
    subtitle_w = (
        text_w(SUBTITLE_STYLE, "角色生日 · 应援色")
        + 6
        + text_w(_ADAPTIVE_CHIP_STYLE.replace(size=13), rqd.color_code)
        + 4
        + 12
    )
    if rqd.timezone:
        subtitle_w += 6 + text_w(SUBTITLE_STYLE, f"· {rqd.timezone}") + 4
    header_w = _BIRTHDAY_SD_WELL + 14 + max(label_w + chips_w, subtitle_w)

    rows = _birthday_event_rows(rqd)
    events_w = 0
    if rows:
        label_col = max(text_w(_BIRTHDAY_EVENT_LABEL_STYLE, row.label) for row in rows) + 4 + 20
        spans = [
            (datetime_from_millis(r.time.start_at, rqd.timezone), datetime_from_millis(r.time.end_at, rqd.timezone))
            for r in rows
        ]
        span_w = max(text_w(_BIRTHDAY_SPAN_STYLE, _birthday_span_text(a, b)) for a, b in spans) + 4
        length_style = TextStyle(font=DEFAULT_BOLD_FONT, size=13)
        length_w = max(text_w(length_style, _birthday_span_length(a, b)) for a, b in spans) + 4 + 12
        events_w = label_col + 12 + span_w + 12 + length_w

    per_row = min(len(rqd.cards), _BIRTHDAY_CARDS_PER_ROW)
    cards_w = per_row * _BIRTHDAY_CARD_THUMB_SIZE + max(0, per_row - 1) * _BIRTHDAY_CARD_SEP

    content = max(header_w, events_w, cards_w)
    return max(_BIRTHDAY_MIN_W, min(_BIRTHDAY_MAX_W, content + 2 * PANEL_PAD))


def _draw_birthday_header(rqd: CharaBirthdayRequest, sd_image, title_image, accent: Color, width: int) -> None:
    """SD chibi in a white well, the name label with region / date / countdown chips, a cheer-colour subtitle."""
    chips = _birthday_header_chips(rqd, accent)
    chips_w = sum(text_w(style, text) + 4 + 16 + 10 for text, _, style in chips)
    text_w_budget = width - 2 * PANEL_PAD - _BIRTHDAY_SD_WELL - 14
    label_w = round(title_image.size[0] * _BIRTHDAY_LABEL_H / max(1, title_image.size[1]))
    label_w = max(40, min(label_w, text_w_budget - chips_w))
    with panel(width, alpha=_BIRTHDAY_PANEL_ALPHA):
        with HSplit().set_content_align("l").set_item_align("c").set_sep(14).set_padding(0):
            with (
                Frame()
                .set_size((_BIRTHDAY_SD_WELL, _BIRTHDAY_SD_WELL))
                .set_content_align("c")
                .set_bg(RoundRectBg(_WELL_FILL, 14, blur_glass=False))
            ):
                ImageBox(sd_image, size=(_BIRTHDAY_SD, _BIRTHDAY_SD), image_size_mode="fit")
            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(6).set_padding(0):
                with HSplit().set_content_align("l").set_item_align("c").set_sep(10).set_padding(0):
                    ImageBox(title_image, size=(label_w, _BIRTHDAY_LABEL_H), image_size_mode="fit")
                    for text, fill, style in chips:
                        chip(text, fill, style=style, radius=11, padding=(8, 3))
                with HSplit().set_content_align("l").set_item_align("c").set_sep(6).set_padding(0):
                    ink(TextBox("角色生日 · 应援色", SUBTITLE_STYLE))
                    chip(rqd.color_code, accent, style=_ADAPTIVE_CHIP_STYLE.replace(size=13), radius=7, padding=(6, 2))
                    tz = (rqd.timezone or "").strip()
                    if tz:
                        ink(TextBox(f"· {tz}", SUBTITLE_STYLE))


def _draw_birthday_events(rqd: CharaBirthdayRequest, accent: Color, width: int) -> None:
    rows = _birthday_event_rows(rqd)
    spans = [
        (datetime_from_millis(row.time.start_at, rqd.timezone), datetime_from_millis(row.time.end_at, rqd.timezone))
        for row in rows
    ]
    tz_label = _birthday_timezone_label(spans[0][0], spans[0][1], rqd.timezone) if spans else ""
    label_w = max(text_w(_BIRTHDAY_EVENT_LABEL_STYLE, row.label) for row in rows) + 4 + 20
    with panel(width, alpha=_BIRTHDAY_PANEL_ALPHA):
        section_header(
            "生日活动",
            accent,
            chips=[("五周年形式", AMBER)] if rqd.is_fifth_anniv else (),
            captions=[f"时区{tz_label}" if tz_label else ""],
        )
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(6).set_padding(0):
            for row, (start, end) in zip(rows, spans, strict=True):
                with HSplit().set_content_align("l").set_item_align("c").set_sep(12).set_padding(0):
                    # Chips are natural width; a fixed-width frame lines the time column up across rows.
                    with Frame().set_content_align("c") as cell:
                        label = chip(
                            row.label,
                            _BIRTHDAY_EVENT_COLORS[row.key],
                            style=_BIRTHDAY_EVENT_LABEL_STYLE,
                            radius=8,
                            padding=(10, 4),
                        )
                    cell.set_size((label_w, label._get_self_size()[1]))
                    ink(TextBox(_birthday_span_text(start, end), _BIRTHDAY_SPAN_STYLE))
                    soft_chip(_birthday_span_length(start, end), DIM, size=13, radius=7, padding=(6, 2), alpha=28)


def _draw_birthday_cards(rqd: CharaBirthdayRequest, card_thumbs, accent: Color, width: int) -> None:
    with panel(width, alpha=_BIRTHDAY_PANEL_ALPHA):
        section_header("生日卡牌", accent, soft_chips=[(f"{len(rqd.cards)} 张", _deep(accent))])
        size = _BIRTHDAY_CARD_THUMB_SIZE
        with (
            Flow()
            .set_w(width - 2 * PANEL_PAD)
            .set_sep(_BIRTHDAY_CARD_SEP, 10)
            .set_content_align("lt")
            .set_item_align("lt")
        ):
            for card, thumb in zip(rqd.cards, card_thumbs, strict=False):
                with VSplit().set_content_align("c").set_item_align("c").set_sep(4).set_padding(0):
                    with RoundClipFrame(10).set_size((size, size)).set_content_align("c"):
                        ImageBox(thumb, size=(size, size), image_size_mode="fill", sampling="linear")
                    ink(TextBox(str(card.id), _BIRTHDAY_ID_STYLE))


def _draw_birthday_calendar(rqd: CharaBirthdayRequest, calendar_icons, accent: Color, width: int) -> None:
    icon = _BIRTHDAY_CALENDAR_ICON_SIZE
    with panel(width, alpha=_BIRTHDAY_PANEL_ALPHA):
        section_header("生日日历", accent, captions=["按下次生日先后排列"])
        with Flow().set_w(width - 2 * PANEL_PAD).set_sep(6, 6).set_content_align("lt").set_item_align("c"):
            for group in _birthday_calendar_cells(rqd.all_characters):
                selected = any(chara.cid == rqd.cid for chara in group)
                fill = _with_alpha(accent, 96) if selected else (255, 255, 255, 110)
                with (
                    VSplit()
                    .set_content_align("c")
                    .set_item_align("c")
                    .set_sep(2)
                    .set_padding((6, 4))
                    .set_bg(RoundRectBg(fill, 10, blur_glass=False))
                ):
                    with HSplit().set_content_align("c").set_item_align("c").set_sep(2).set_padding(0):
                        for chara in group:
                            ImageBox(
                                calendar_icons[chara.cid], size=(icon, icon), image_size_mode="fill", sampling="linear"
                            )
                    style = (
                        _BIRTHDAY_DATE_STYLE.replace(font=DEFAULT_BOLD_FONT, color=_deep(accent))
                        if selected
                        else _BIRTHDAY_DATE_STYLE
                    )
                    ink(TextBox(f"{group[0].month}/{group[0].day}", style))


async def _build_chara_birthday_canvas(rqd: CharaBirthdayRequest) -> Canvas:
    r"""_build_chara_birthday_canvas

    合成角色生日图片: 生日卡面作全页背景; 头部(SD + 名字标签 + 服务器/日期/倒计时) → 活动时间 → 生日卡牌 → 生日日历
    """
    card_image, sd_image, title_image, card_thumbs, calendar_icons, _ = await _load_chara_birthday_assets(rqd)
    accent = _birthday_accent(rqd.color_code)
    width = _birthday_page_width(rqd, title_image, accent)

    # The newest birthday card's art is the page background, as before; a missing art falls back to the
    # plain triangle background rather than a full-page placeholder.
    background = SEKAI_BLUE_BG if isinstance(card_image, MissingImageRef) else ImageBg(card_image)
    with Canvas(bg=background).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(_PAGE_SEP).set_padding(_PAGE_PAD):
            _draw_birthday_header(rqd, sd_image, title_image, accent, width)
            _draw_birthday_events(rqd, accent, width)
            if rqd.cards:
                _draw_birthday_cards(rqd, card_thumbs, accent, width)
            if rqd.all_characters:
                _draw_birthday_calendar(rqd, calendar_icons, accent, width)

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


# --------------------------- 别名列表 --------------------------- #

_ALIAS_WIDTHS = (600, 680, 760, 840, 920, 1000)
_ALIAS_TARGET_H = 720  # with a standing picture: the narrowest column no taller than this
_ALIAS_MAX_ROWS = 8  # without one: the narrowest width that keeps the chips within this many rows
_ALIAS_CHIP_STYLE = TextStyle(font=DEFAULT_FONT, size=18, color=TEXT)
_ALIAS_CHIP_PAD = (12, 6)
_ALIAS_CHIP_SEP = 8
_ALIAS_JACKET_WELL = 64
_ALIAS_JACKET = 54
_ALIAS_TRIM_ALPHA_FLOOR = 36
_ALIAS_TRIM_MAX_W = 920
_ALIAS_TRIM_MIN_H = 500
_ALIAS_TRIM_MAX_H = 920
_ALIAS_TRIM_EXTRA_H = 24  # the picture stands this much taller than the column it is drawn beside
# The picture may lean this far over the column: no more than the panel padding, so hair never covers a chip.
_ALIAS_TRIM_OVERLAP = 12
# The picture's feet sit on the page's bottom edge: it hangs below its frame by the page and canvas padding.
_ALIAS_TRIM_BOTTOM_HANG = _PAGE_PAD + BG_PADDING
_ALIAS_TITLE_MIN_SIZE = 24
_ALIAS_TITLE_LINES = 2
_ALIAS_MUSIC_ACCENT: Color = (64, 132, 226, 255)
_ALIAS_CHARA_FALLBACK_ACCENT: Color = (112, 122, 146, 255)


def _resolve_alias_accent(entity_label: str, entity_id: int) -> Color:
    if "角色" in entity_label:
        if color_code := CHARACTER_COLOR_CODE.get(entity_id):
            return tuple(color_code_to_rgb(color_code))
        return _ALIAS_CHARA_FALLBACK_ACCENT
    return _ALIAS_MUSIC_ACCENT


def _resolve_alias_trim_path(rqd: AliasListRequest) -> str | None:
    if rqd.character_silhouette_path and rqd.character_silhouette_path.strip():
        return rqd.character_silhouette_path.strip()
    if rqd.character_trim_path and rqd.character_trim_path.strip():
        return rqd.character_trim_path.strip()
    return None


def _prepare_alias_trim_image(img: ImageSource) -> AlphaTrimImageBox:
    bounds = probe_alpha_bounds(img) or (0, 0, img.width, img.height)
    return AlphaTrimImageBox(img, bounds, _ALIAS_TRIM_ALPHA_FLOOR, use_alpha_blend=True)


def _alias_chip_w(alias: str) -> int:
    return text_w(_ALIAS_CHIP_STYLE, alias) + 4 + 2 * _ALIAS_CHIP_PAD[0]


def _alias_flow_rows(aliases: list[str], flow_w: int) -> int:
    """How many rows the chips take at ``flow_w`` (a chip wider than the row is capped to it)."""
    rows, used = 0, None
    for alias in aliases:
        w = min(_alias_chip_w(alias), flow_w)
        if used is None or used + _ALIAS_CHIP_SEP + w > flow_w:
            rows += 1
            used = w
        else:
            used += _ALIAS_CHIP_SEP + w
    return rows


def _alias_width_without_trim(aliases: list[str]) -> int:
    for width in _ALIAS_WIDTHS:
        if _alias_flow_rows(aliases, width - 2 * PANEL_PAD) <= _ALIAS_MAX_ROWS:
            return width
    return _ALIAS_WIDTHS[-1]


def _draw_alias_name(name: str, budget: int) -> None:
    """The entity name on one line, stepping down to the minimum size; a longer name wraps onto a second line."""
    style = fit_style(name, TITLE_STYLE, budget, min_size=_ALIAS_TITLE_MIN_SIZE)
    if text_w(style, name) + 4 <= budget:
        ink(TextBox(name, style).set_w(max(60, min(text_w(style, name) + 4, budget))))
        return
    TextBox(name, style, line_count=_ALIAS_TITLE_LINES, overflow="shrink").set_w(max(60, budget))


def _draw_alias_header(rqd: AliasListRequest, accent: Color, jacket_img, alias_count: int, width: int) -> None:
    """Jacket well (songs) or accent bar, the entity name with its ID chip, and a dim subtitle."""
    id_chip_text = f"{rqd.entity_label} {rqd.entity_id}"
    visual_w = _ALIAS_JACKET_WELL if jacket_img is not None else 6
    text_budget = width - 2 * PANEL_PAD - visual_w - 14
    title_budget = text_budget - (text_w(_TITLE_CHIP_STYLE, id_chip_text) + 4 + 16 + 10)
    subtitle = f"{rqd.title} · 已审核通过 {alias_count} 条 · 过多时自动转为图片返回"
    with panel(width):
        with HSplit().set_content_align("l").set_item_align("c").set_sep(14).set_padding(0):
            if jacket_img is not None:
                with (
                    Frame()
                    .set_size((_ALIAS_JACKET_WELL, _ALIAS_JACKET_WELL))
                    .set_content_align("c")
                    .set_bg(RoundRectBg(_WELL_FILL, 14, blur_glass=False))
                ):
                    with RoundClipFrame(8).set_size((_ALIAS_JACKET, _ALIAS_JACKET)).set_content_align("c"):
                        ImageBox(jacket_img, size=(_ALIAS_JACKET, _ALIAS_JACKET), image_size_mode="fill")
            else:
                Spacer(w=6, h=48).set_bg(RoundRectBg(accent, 3, blur_glass=False))
            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(2).set_padding(0):
                with HSplit().set_content_align("l").set_item_align("c").set_sep(10).set_padding(0):
                    _draw_alias_name(rqd.entity_name.strip() or "-", title_budget)
                    chip(id_chip_text, accent, style=_ADAPTIVE_CHIP_STYLE, radius=11, padding=(8, 3))
                TextBox(subtitle, SUBTITLE_STYLE, overflow="shrink").set_w(
                    max(40, min(text_w(SUBTITLE_STYLE, subtitle) + 4, text_budget))
                )


def _draw_alias_chips(aliases: list[str], accent: Color, width: int) -> None:
    flow_w = width - 2 * PANEL_PAD
    with panel(width):
        section_header("别名列表", accent, soft_chips=[(f"{len(aliases)} 条", _deep(accent))])
        with (
            Flow().set_w(flow_w).set_sep(_ALIAS_CHIP_SEP, _ALIAS_CHIP_SEP).set_content_align("lt").set_item_align("lt")
        ):
            for alias in aliases:
                chip(
                    alias,
                    (255, 255, 255, 200),
                    style=_ALIAS_CHIP_STYLE,
                    radius=10,
                    padding=_ALIAS_CHIP_PAD,
                    stroke=_with_alpha(accent, 120),
                    max_w=flow_w,
                )


def _build_alias_column(rqd: AliasListRequest, aliases: list[str], accent: Color, jacket_img, width: int) -> VSplit:
    """The header + chips column, built detached so it can be measured before the page is laid out."""
    token = Widget._thread_local.set(None)
    try:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(_PAGE_SEP).set_padding(0) as column:
            _draw_alias_header(rqd, accent, jacket_img, len(aliases), width)
            _draw_alias_chips(aliases, accent, width)
        return column
    finally:
        Widget._thread_local.reset(token)


def _resolve_alias_column_width(
    rqd: AliasListRequest, aliases: list[str], accent: Color, jacket_img
) -> tuple[int, int]:
    """With a standing picture: the narrowest width whose column is no taller than the target, else the least over."""
    best: tuple[int, int, int] | None = None
    for width in _ALIAS_WIDTHS:
        height = _build_alias_column(rqd, aliases, accent, jacket_img, width)._get_self_size()[1]
        if height <= _ALIAS_TARGET_H:
            return width, height
        if best is None or height < best[0]:
            best = (height, width, height)
    assert best is not None
    return best[1], best[2]


def _resolve_alias_trim_metrics(trim_img: AlphaTrimImageBox, column_h: int) -> tuple[int, int, int]:
    """``(frame_w, frame_h, display_h)``: the picture is large (at least the minimum height, a little taller than
    the column, capped), bottom-aligned so its feet touch the page's bottom edge, and the frame is narrower than
    the picture only by the overlap, so it leans over the column's padding but never over a chip. The frame is
    tall enough for the picture's head to stay on the page when the column is short."""
    width, height = trim_img.natural_size
    aspect = width / max(1, height)
    display_h = max(_ALIAS_TRIM_MIN_H, min(_ALIAS_TRIM_MAX_H, column_h + _ALIAS_TRIM_EXTRA_H))
    rendered_w = max(1, round(display_h * aspect))
    if rendered_w > _ALIAS_TRIM_MAX_W:
        rendered_w = _ALIAS_TRIM_MAX_W
        display_h = max(1, round(rendered_w / max(aspect, 1e-6)))
    frame_h = max(column_h, display_h - _ALIAS_TRIM_BOTTOM_HANG)
    return rendered_w - _ALIAS_TRIM_OVERLAP, frame_h, display_h


def _build_alias_trim_panel(trim_img: AlphaTrimImageBox, column_h: int) -> Frame:
    token = Widget._thread_local.set(None)
    try:
        frame_w, frame_h, display_h = _resolve_alias_trim_metrics(trim_img, column_h)
        trim_panel = Frame().set_size((frame_w, frame_h)).set_content_align("rb").set_allow_draw_outside(True)
        trim_img.image_size_mode = "fit"
        trim_panel.add_item(trim_img.set_size((None, display_h)).set_offset((0, _ALIAS_TRIM_BOTTOM_HANG)))
        return trim_panel
    finally:
        Widget._thread_local.reset(token)


async def _build_alias_list_canvas(rqd: AliasListRequest) -> Canvas:
    aliases = [alias.strip() for alias in rqd.aliases if alias and alias.strip()]
    accent = _resolve_alias_accent(rqd.entity_label, rqd.entity_id)
    jacket_img = None
    if rqd.music_jacket_path:
        jacket_img = await get_asset_image_ref(ASSETS_BASE_DIR, rqd.music_jacket_path)
    trim_img = None
    trim_path = _resolve_alias_trim_path(rqd)
    if trim_path:
        try:
            trim_source = await get_asset_image_ref(ASSETS_BASE_DIR, trim_path, on_missing="raise")
            trim_img = await run_in_pool(_prepare_alias_trim_image, trim_source)
        except (FileNotFoundError, OSError, ValueError):
            trim_img = None

    if trim_img is None:
        width = _alias_width_without_trim(aliases)
        column = _build_alias_column(rqd, aliases, accent, jacket_img, width)
        trim_panel = None
    else:
        width, column_h = _resolve_alias_column_width(rqd, aliases, accent, jacket_img)
        column = _build_alias_column(rqd, aliases, accent, jacket_img, width)
        trim_panel = _build_alias_trim_panel(trim_img, column_h)

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(_PAGE_SEP).set_padding(_PAGE_PAD) as root:
            if trim_panel is None:
                root.add_item(column)
            else:
                with HSplit().set_content_align("lt").set_item_align("t").set_sep(0).set_padding(0) as row:
                    row.add_item(column)
                    row.add_item(trim_panel)

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


def _append_command_help_body_line(
    lines: list[_CommandHelpLine],
    trimmed_right: str,
    trimmed: str,
    *,
    in_code: bool,
) -> None:
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
        return
    if not trimmed:
        lines.append(_CommandHelpLine("", DEFAULT_FONT, 10, gap_before=10))
        return
    if trimmed.startswith(("import ", "const ", "<")):
        return

    bullet = _command_help_bullet(trimmed)
    if bullet is not None:
        cleaned = _clean_command_help_inline(bullet)
        if "：" in cleaned or ":" in cleaned:
            _append_command_help_definition_line(lines, cleaned)
        else:
            _append_command_help_wrapped_line(
                lines,
                cleaned,
                font_name=DEFAULT_FONT,
                size=21,
                indent=34,
                fill=(50, 61, 78, 255),
                gap_before=7,
            )
        return

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
        return
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
        return
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
        return
    _append_command_help_wrapped_line(
        lines,
        _clean_command_help_inline(trimmed),
        font_name=DEFAULT_FONT,
        size=21,
        fill=(50, 61, 78, 255),
        gap_before=7,
    )


def _flush_command_help_section(
    sections: list[_CommandHelpSection],
    section_title: str,
    lines: list[_CommandHelpLine],
) -> None:
    while lines and not lines[0].text:
        lines.pop(0)
    while lines and not lines[-1].text:
        lines.pop()
    if lines:
        sections.append(_CommandHelpSection(section_title, list(lines)))
    lines.clear()
