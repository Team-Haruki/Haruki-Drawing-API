import asyncio
import logging
import time
from datetime import datetime

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from src.sekai.base.draw import BG_PADDING, SEKAI_BLUE_BG, add_request_watermark, roundrect_bg
from src.sekai.base.painter import DEFAULT_BOLD_FONT, DEFAULT_FONT, DEFAULT_HEAVY_FONT
from src.sekai.base.plot import Canvas, Frame, Grid, HSplit, ImageBox, Spacer, TextBox, TextStyle, VSplit
from src.sekai.base.timezone import request_now
from src.sekai.base.utils import (
    build_rendered_image_cache_key,
    get_composed_image_cached,
    get_composed_image_disk_cached,
    get_img_from_path,
    get_readable_timedelta,
    put_composed_image_cache,
    put_composed_image_disk_cache,
)
from src.settings import ASSETS_BASE_DIR, FONT_DIR

from .model import VLiveBrief, VLiveListRequest

logger = logging.getLogger(__name__)
_perf_logger = logging.getLogger("vlive.draw.perf")
_VLIVE_LIST_ENTRY_CACHE_NAMESPACE = "vlive_list_entry"


def _format_time(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _format_relative(target: datetime | None, now: datetime) -> str:
    if target is None:
        return "-"

    delta = target - now
    seconds = int(delta.total_seconds())
    if -60 < seconds < 60:
        return "刚刚"
    if seconds > 0:
        return f"{get_readable_timedelta(delta)}后"
    return f"{get_readable_timedelta(now - target)}前"


def _build_vlive_time_text(label: str, target: datetime | None, now: datetime) -> str:
    return f"{label} {_format_time(target)} ({_format_relative(target, now)})"


def _build_vlive_status_text(vlive: VLiveBrief, now: datetime) -> str:
    if vlive.living:
        return "当前Live进行中!"
    if vlive.current_start_at is not None:
        return f"下一场: {_format_relative(vlive.current_start_at, now)}"
    return "已结束"


def _build_vlive_entry_cache_key(vlive: VLiveBrief, now: datetime) -> str:
    return build_rendered_image_cache_key(
        "vlive_list_entry",
        vlive,
        extra={
            "version": 2,
            "state": "living" if vlive.living else "upcoming",
            "bucket": now.strftime("%Y%m%d%H%M"),
        },
    )


def _vlive_entry_bg_fill(vlive: VLiveBrief) -> tuple[int, int, int, int]:
    return (255, 244, 251, 160) if vlive.living else (255, 248, 253, 148)


def _load_ticket_font(font_name: str, size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    font_dir = FONT_DIR
    for suffix in ("", ".otf", ".ttf"):
        candidate = font_dir / f"{font_name}{suffix}"
        if not candidate.is_file():
            continue
        try:
            return ImageFont.truetype(str(candidate), size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def _fit_crop(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(img.convert("RGBA"), size, method=Image.Resampling.LANCZOS)


def _measure_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont | ImageFont.FreeTypeFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    max_width: int,
    max_lines: int,
) -> list[str]:
    words = text.split()
    if not words:
        return [text]

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if _measure_text(draw, candidate, font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = word
        else:
            lines.append(word)
            current = ""
        if len(lines) >= max_lines - 1:
            break

    tail_words = words[len(" ".join(lines + ([current] if current else [])).split()):]
    if tail_words:
        current = f"{current} {' '.join(tail_words)}".strip()
    if current:
        lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if len(lines) == max_lines and _measure_text(draw, lines[-1], font) > max_width:
        line = lines[-1]
        while line and _measure_text(draw, line + "...", font) > max_width:
            line = line[:-1].rstrip()
        lines[-1] = (line or lines[-1]) + "..."
    return lines


def _ticket_title(name: str) -> str:
    title = name.strip()
    for suffix in (
        " 后日谈・演唱会",
        " 后日谈 · 演唱会",
        " 后日谈·演唱会",
        " 後日談・演唱会",
        " 後日談・演唱會",
        " 演唱会",
        " 演唱會",
    ):
        if title.endswith(suffix):
            return title[: -len(suffix)].strip()
    return title


def _ticket_subtitle(name: str) -> str:
    lowered = name.lower()
    if "birthday" in lowered or "生日" in name or "誕生日" in name:
        return "BIRTHDAY LIVE"
    if "后日谈" in name or "後日談" in name:
        return "后日谈 · 演唱会"
    if "演唱会" in name or "演唱會" in name:
        return "演唱会"
    return "VIRTUAL LIVE"


def _ticket_label(name: str) -> str:
    lowered = name.lower()
    if "birthday" in lowered or "生日" in name or "誕生日" in name:
        return "BIRTHDAY\nLIVE"
    if "后日谈" in name or "後日談" in name:
        return "AFTER\nLIVE"
    return "VIRTUAL\nLIVE"


def _build_ticket_banner(vlive: VLiveBrief, banner: Image.Image | None) -> Image.Image:
    width, height = 724, 134
    tag_width = 102
    gap = 12
    left_width = width - tag_width - gap
    corner_radius = 22

    ticket = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    left_panel = Image.new("RGBA", (left_width, height), (240, 230, 247, 255))

    if banner is not None:
        left_panel = _fit_crop(banner, (left_width, height))
    else:
        draw = ImageDraw.Draw(left_panel)
        draw.rounded_rectangle((0, 0, left_width - 1, height - 1), radius=corner_radius, fill=(228, 222, 236, 255))

    left_mask = _rounded_mask((left_width, height), corner_radius)
    left_panel.putalpha(left_mask)
    ticket.paste(left_panel, (0, 0), left_panel)

    overlay = Image.new("RGBA", (left_width, height), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle((0, height - 50, 280, height - 1), radius=18, fill=(34, 38, 78, 165))
    overlay_draw.rounded_rectangle((0, 0, left_width - 1, height - 1), radius=corner_radius, outline=(255, 255, 255, 155), width=2)
    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=0.6))
    overlay.putalpha(ImageChops.multiply(overlay.split()[3], left_mask))
    ticket.paste(overlay, (0, 0), overlay)

    tag_fill = (207, 208, 229, 228) if not vlive.living else (202, 212, 237, 236)
    tag_panel = Image.new("RGBA", (tag_width, height), (0, 0, 0, 0))
    tag_draw = ImageDraw.Draw(tag_panel)
    tag_draw.rounded_rectangle((0, 0, tag_width - 1, height - 1), radius=corner_radius, fill=tag_fill)
    tag_draw.rounded_rectangle((0, 0, tag_width - 1, height - 1), radius=corner_radius, outline=(255, 255, 255, 160), width=2)
    ticket.paste(tag_panel, (left_width + gap, 0), tag_panel)

    draw = ImageDraw.Draw(ticket)
    seam_x = left_width + gap // 2
    for y in range(18, height - 18, 9):
        draw.rounded_rectangle((seam_x - 1, y, seam_x + 1, y + 4), radius=2, fill=(255, 255, 255, 150))

    title_font = _load_ticket_font(DEFAULT_HEAVY_FONT, 18)
    subtitle_font = _load_ticket_font(DEFAULT_BOLD_FONT, 15)
    tag_font = _load_ticket_font(DEFAULT_HEAVY_FONT, 22)

    title_area_x = 22
    title_area_y = 18
    title_max_width = 250
    title_lines = _wrap_text(draw, _ticket_title(vlive.name), title_font, title_max_width, 2)
    for idx, line in enumerate(title_lines[:2]):
        draw.text((title_area_x, title_area_y + idx * 24), line, font=title_font, fill=(255, 255, 255, 245))

    draw.text((title_area_x, height - 34), _ticket_subtitle(vlive.name), font=subtitle_font, fill=(255, 255, 255, 235))

    tag_text = _ticket_label(vlive.name)
    tag_text_img = Image.new("RGBA", (height, tag_width), (0, 0, 0, 0))
    tag_text_draw = ImageDraw.Draw(tag_text_img)
    bbox = tag_text_draw.multiline_textbbox((0, 0), tag_text, font=tag_font, spacing=4, align="center")
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    tag_text_draw.multiline_text(
        ((height - text_w) / 2 - bbox[0], (tag_width - text_h) / 2 - bbox[1]),
        tag_text,
        font=tag_font,
        fill=(145, 149, 180, 235),
        spacing=4,
        align="center",
    )
    rotated = tag_text_img.rotate(90, expand=True)
    tag_x = left_width + gap + (tag_width - rotated.width) // 2
    tag_y = (height - rotated.height) // 2
    ticket.paste(rotated, (tag_x, tag_y), rotated)

    return ticket


async def _preload_vlive_entry_assets(vlive: VLiveBrief) -> dict[str, object]:
    tasks = {}
    if vlive.banner_path:
        tasks["banner"] = get_img_from_path(ASSETS_BASE_DIR, vlive.banner_path)
    if vlive.rewards:
        tasks["rewards"] = asyncio.gather(
            *[get_img_from_path(ASSETS_BASE_DIR, item.image_path) for item in vlive.rewards]
        )
    if vlive.characters:
        tasks["characters"] = asyncio.gather(
            *[get_img_from_path(ASSETS_BASE_DIR, item.icon_path) for item in vlive.characters]
        )
    if not tasks:
        return {}
    keys = list(tasks.keys())
    values = await asyncio.gather(*tasks.values())
    return dict(zip(keys, values))


async def _compose_vlive_entry_image(
    vlive: VLiveBrief,
    loaded: dict[str, object],
    now: datetime,
) -> Image.Image:
    info_style = TextStyle(font=DEFAULT_FONT, size=18, color=(52, 52, 58))
    section_style = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(42, 42, 48))
    quantity_style = TextStyle(font=DEFAULT_HEAVY_FONT, size=14, color=(60, 60, 68))

    rewards = loaded.get("rewards", [])
    characters = loaded.get("characters", [])
    banner = loaded.get("banner")
    ticket_banner = _build_ticket_banner(vlive, banner)

    with Canvas().set_padding(0) as canvas:
        with VSplit().set_padding(0).set_sep(14).set_item_align("l").set_content_align("l"):
            ImageBox(ticket_banner, size=(724, None), use_alpha_blend=True)

            with HSplit().set_padding(0).set_sep(16).set_item_align("lt").set_content_align("lt"):
                with VSplit().set_padding(0).set_sep(12).set_item_align("l").set_content_align("l"):
                    with HSplit().set_padding(0).set_sep(28).set_item_align("t").set_content_align("l"):
                        if rewards:
                            with VSplit().set_padding(0).set_sep(8).set_item_align("l").set_content_align("l"):
                                TextBox("参与奖励", section_style)
                                with HSplit().set_padding(0).set_sep(10).set_item_align("t").set_content_align("l"):
                                    for reward_model, reward_image in zip(vlive.rewards or [], rewards):
                                        with VSplit().set_padding(0).set_sep(4).set_item_align("c").set_content_align("c"):
                                            ImageBox(reward_image, size=(48, 48), use_alpha_blend=True)
                                            TextBox(f"x{max(1, reward_model.quantity)}", quantity_style)

                        if characters:
                            with VSplit().set_padding(0).set_sep(8).set_item_align("l").set_content_align("l"):
                                TextBox("出演角色", section_style)
                                with Grid(
                                    col_count=max(1, min(10, len(characters)))
                                ).set_padding(0).set_sep(6, 6).set_item_align("l").set_content_align("l"):
                                    for character_image in characters:
                                        ImageBox(character_image, size=(30, 30), use_alpha_blend=True)

                with VSplit().set_padding((0, 6)).set_sep(10).set_item_align("l").set_content_align("l"):
                    TextBox(_build_vlive_time_text("开始于", vlive.start_at, now), info_style).set_w(330)
                    TextBox(_build_vlive_time_text("结束于", vlive.end_at, now), info_style).set_w(330)
                    TextBox(
                        f"{_build_vlive_status_text(vlive, now)} |剩余场次: {vlive.rest_count}",
                        info_style,
                    ).set_w(330)
                    if not rewards and not characters:
                        Spacer(w=1, h=1)

    return await canvas.get_img()


async def _get_vlive_list_entry_image(vlive: VLiveBrief, now: datetime) -> Image.Image:
    cache_key = _build_vlive_entry_cache_key(vlive, now)

    cached = get_composed_image_cached(cache_key)
    if cached is not None:
        _perf_logger.info("vlive/list entry memory hit: id=%s", vlive.id)
        return cached

    disk_cached = get_composed_image_disk_cached(_VLIVE_LIST_ENTRY_CACHE_NAMESPACE, cache_key)
    if disk_cached is not None:
        put_composed_image_cache(cache_key, disk_cached)
        _perf_logger.info("vlive/list entry disk hit: id=%s", vlive.id)
        return disk_cached

    loaded = await _preload_vlive_entry_assets(vlive)
    image = await _compose_vlive_entry_image(vlive, loaded, now)
    put_composed_image_cache(cache_key, image)
    put_composed_image_disk_cache(_VLIVE_LIST_ENTRY_CACHE_NAMESPACE, cache_key, image)
    _perf_logger.info("vlive/list entry miss: id=%s size=%dx%d", vlive.id, image.width, image.height)
    return image


async def compose_vlive_list_image(rqd: VLiveListRequest) -> Image.Image:
    lives = rqd.lives
    now = request_now(rqd.timezone)

    _t0 = time.perf_counter()
    entry_images = await asyncio.gather(*[_get_vlive_list_entry_image(vlive, now) for vlive in lives]) if lives else []
    _t_entries = time.perf_counter() - _t0

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_padding(0).set_sep(16).set_item_align("lt").set_content_align("lt"):
            for vlive, entry_image in zip(lives, entry_images):
                with Frame().set_w(760).set_padding(18).set_bg(
                    roundrect_bg(
                        _vlive_entry_bg_fill(vlive),
                        radius=16,
                        blur_glass_kwargs={"blur": 8},
                    )
                ):
                    ImageBox(entry_image)

    add_request_watermark(canvas, rqd)
    _t0 = time.perf_counter()
    image = await canvas.get_img()
    _t_render = time.perf_counter() - _t0
    _perf_logger.info(
        "vlive/list total: entries=%.3fs render=%.3fs lives=%d image=%dx%d",
        _t_entries,
        _t_render,
        len(lives),
        image.width,
        image.height,
    )
    return image
