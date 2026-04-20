import asyncio
from collections import OrderedDict
from datetime import datetime, timedelta
import io
import logging
import os
from os.path import join as pjoin
from pathlib import Path
import threading
from typing import Literal
from uuid import uuid4

import aiohttp
from PIL import Image, ImageDraw, ImageFont

from src.settings import (
    ASSETS_BASE_DIR,
    DEFAULT_BOLD_FONT,
    DEFAULT_HEAVY_FONT,
    DEFAULT_THREAD_POOL_SIZE,
    FONT_DIR,
    IMAGE_CACHE_MAX_BYTES,
    IMAGE_CACHE_SIZE,
    SCREENSHOT_API_PATH,
    THUMB_CACHE_MAX_BYTES,
    THUMB_CACHE_SIZE,
    TMP_PATH,
)

logger = logging.getLogger(__name__)

MissingImageMode = Literal["raise", "placeholder"]


def get_readable_timedelta(delta: timedelta, precision: str = "m", use_en_unit: bool = False) -> str:
    """
    将时间段转换为可读字符串
    """
    match precision:
        case "s":
            precision = 3
        case "m":
            precision = 2
        case "h":
            precision = 1
        case "d":
            precision = 0

    s = int(delta.total_seconds())
    if s < 0:
        return "0秒" if not use_en_unit else "0s"
    d = s // (24 * 3600)
    s %= 24 * 3600
    h = s // 3600
    s %= 3600
    m = s // 60
    s %= 60

    ret = ""
    if d > 0:
        ret += f"{d}天" if not use_en_unit else f"{d}d"
    if h > 0 and (precision >= 1 or not ret):
        ret += f"{h}小时" if not use_en_unit else f"{h}h"
    if m > 0 and (precision >= 2 or not ret):
        ret += f"{m}分钟" if not use_en_unit else f"{m}m"
    if s > 0 and (precision >= 3 or not ret):
        ret += f"{s}秒" if not use_en_unit else f"{s}s"
    return ret


async def get_img_from_path(
    base_path: Path,
    path: str | None,
    on_missing: MissingImageMode = "placeholder",
) -> Image.Image:
    """
    通过路径获取图片
    """
    if path is None or path.strip() == "":
        if on_missing == "placeholder":
            _log_missing_image_once(path, "empty-path")
            return _get_missing_placeholder_image(path)
        raise ValueError("图片路径不能为空(None)")

    try:
        return await run_in_pool(_load_image_from_path_sync, base_path, path)
    except (FileNotFoundError, OSError) as exc:
        if on_missing == "placeholder":
            _log_missing_image_once(path, exc)
            return _get_missing_placeholder_image(path)
        raise


def _open_image_copy(path: Path) -> Image.Image:
    with Image.open(path) as img:
        img.load()
        return img.copy()


_image_cache_lock = threading.RLock()
# cache key: (path, mtime_ns, file_size, target_w, target_h)
# target (0, 0) means original size (no resize)
_image_cache: OrderedDict[tuple[str, int, int, int, int], tuple[Image.Image, int]] = OrderedDict()
_image_cache_total_bytes = 0

# 缩略图专用缓存：路径含 "thumbnail" 的图片路由到此缓存，避免被大图驱逐
_thumb_cache_lock = threading.RLock()
_thumb_cache: OrderedDict[tuple[str, int, int, int, int], tuple[Image.Image, int]] = OrderedDict()
_thumb_cache_total_bytes = 0

_missing_placeholder_lock = threading.RLock()
_missing_placeholder_cache: dict[str, Image.Image] = {}
_missing_placeholder_logged: set[str] = set()


def _log_missing_image_once(path: str | None, reason: str | BaseException) -> None:
    if isinstance(reason, BaseException):
        reason_text = f"{reason.__class__.__name__}: {reason}"
    else:
        reason_text = reason

    key = f"{path or '<empty>'}|{reason_text}"
    with _missing_placeholder_lock:
        if key in _missing_placeholder_logged:
            return
        _missing_placeholder_logged.add(key)

    logger.warning("图片素材缺失，已使用问号占位图: %s (%s)", path or "<empty>", reason_text)


def _guess_missing_placeholder_variant(path: str | None) -> str:
    normalized = (path or "").replace("\\", "/").lower()

    if (
        "banner_event" in normalized
        or "event_banner" in normalized
        or ("/banner/" in normalized and "event" in normalized)
    ):
        return "event_banner"
    if any(token in normalized for token in ("banner", "logo", "header", "title", "word_img", "word/")):
        return "wide"
    if any(token in normalized for token in ("background", "story_bg", "event_bg", "/bg/", "_bg", "bg_")):
        return "landscape"
    if any(token in normalized for token in ("portrait", "standing", "fullbody", "full_body")):
        return "portrait"
    return "square"


def _load_placeholder_font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    font_dir = Path(FONT_DIR)
    for font_name in (DEFAULT_HEAVY_FONT, DEFAULT_BOLD_FONT):
        for candidate in (font_dir / font_name, font_dir / f"{font_name}.ttf", font_dir / f"{font_name}.otf"):
            if not candidate.is_file():
                continue
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    center: tuple[float, float],
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = center[0] - text_w / 2 - bbox[0]
    y = center[1] - text_h / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=fill)


def _build_missing_placeholder_image(variant: str) -> Image.Image:
    sizes = {
        "square": (512, 512),
        "portrait": (512, 768),
        "landscape": (768, 432),
        "event_banner": (900, 400),
        "wide": (960, 320),
    }
    width, height = sizes.get(variant, sizes["square"])
    short_side = min(width, height)

    outer_pad = max(18, short_side // 20)
    inner_pad = max(12, short_side // 14)
    radius_outer = max(24, short_side // 10)
    radius_inner = max(18, short_side // 14)
    border_width = max(3, short_side // 96)
    line_width = max(4, short_side // 72)

    canvas = Image.new("RGBA", (width, height), (244, 247, 250, 255))
    draw = ImageDraw.Draw(canvas)

    draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=radius_outer,
        fill=(236, 240, 245, 255),
        outline=(210, 216, 224, 255),
        width=border_width,
    )
    draw.rounded_rectangle(
        (outer_pad, outer_pad, width - outer_pad - 1, height - outer_pad - 1),
        radius=radius_inner,
        fill=(251, 252, 253, 255),
        outline=(190, 198, 208, 255),
        width=border_width,
    )

    left = outer_pad + inner_pad
    top = outer_pad + inner_pad
    right = width - outer_pad - inner_pad
    bottom = height - outer_pad - inner_pad
    draw.line((left, top, right, bottom), fill=(228, 232, 238, 255), width=line_width)
    draw.line((left, bottom, right, top), fill=(228, 232, 238, 255), width=line_width)

    qmark_font = _load_placeholder_font(max(48, int(short_side * 0.56)))
    qmark_center = (width / 2, height / 2 - short_side * 0.04)
    _draw_centered_text(draw, "?", (qmark_center[0] + 4, qmark_center[1] + 6), qmark_font, (255, 255, 255, 220))
    _draw_centered_text(draw, "?", qmark_center, qmark_font, (118, 128, 140, 255))

    label_font = _load_placeholder_font(max(16, int(short_side * 0.08)))
    _draw_centered_text(draw, "MISSING", (width / 2, height - outer_pad - short_side * 0.1), label_font, (142, 150, 160, 255))
    return canvas


def _get_missing_placeholder_image(path: str | None) -> Image.Image:
    variant = _guess_missing_placeholder_variant(path)
    with _missing_placeholder_lock:
        cached = _missing_placeholder_cache.get(variant)
        if cached is None:
            cached = _build_missing_placeholder_image(variant)
            _missing_placeholder_cache[variant] = cached
        return cached.copy()


def _estimate_image_bytes(img: Image.Image) -> int:
    bpp = {
        "1": 1,
        "L": 1,
        "P": 1,
        "LA": 2,
        "RGB": 3,
        "RGBA": 4,
        "CMYK": 4,
        "I": 4,
        "F": 4,
        "I;16": 2,
    }.get(img.mode, len(img.getbands()) or 4)
    return img.width * img.height * bpp


def _is_thumbnail_path(path: str) -> bool:
    return "thumbnail" in path


def _cache_enabled(path: str) -> bool:
    """判断给定路径是否有可用的缓存。"""
    if _is_thumbnail_path(path):
        return THUMB_CACHE_SIZE > 0 and THUMB_CACHE_MAX_BYTES > 0
    return IMAGE_CACHE_SIZE > 0 and IMAGE_CACHE_MAX_BYTES > 0


def _load_image_cached(
    path: str, mtime_ns: int, size: int, target_w: int = 0, target_h: int = 0
) -> Image.Image | None:
    cache_key = (path, mtime_ns, size, target_w, target_h)
    if _is_thumbnail_path(path):
        lock, cache = _thumb_cache_lock, _thumb_cache
    else:
        lock, cache = _image_cache_lock, _image_cache
    with lock:
        entry = cache.get(cache_key)
        if entry is None:
            return None
        image, _ = entry
        cache.move_to_end(cache_key)
        return image.copy()


def _put_image_cache(
    path: str, mtime_ns: int, size: int, image: Image.Image, target_w: int = 0, target_h: int = 0
) -> None:
    global _image_cache_total_bytes, _thumb_cache_total_bytes

    is_thumb = _is_thumbnail_path(path)
    if is_thumb:
        lock, cache = _thumb_cache_lock, _thumb_cache
        max_size, max_bytes = THUMB_CACHE_SIZE, THUMB_CACHE_MAX_BYTES
    else:
        lock, cache = _image_cache_lock, _image_cache
        max_size, max_bytes = IMAGE_CACHE_SIZE, IMAGE_CACHE_MAX_BYTES

    if max_size <= 0 or max_bytes <= 0:
        return

    cache_key = (path, mtime_ns, size, target_w, target_h)
    cache_bytes = _estimate_image_bytes(image)
    with lock:
        old_entry = cache.pop(cache_key, None)
        if old_entry is not None:
            old_image, old_bytes = old_entry
            if is_thumb:
                _thumb_cache_total_bytes -= old_bytes
            else:
                _image_cache_total_bytes -= old_bytes
            old_image.close()

        cache[cache_key] = (image, cache_bytes)
        if is_thumb:
            _thumb_cache_total_bytes += cache_bytes
        else:
            _image_cache_total_bytes += cache_bytes

        # 双阈值驱逐：条目数和总字节数都受控
        current_bytes = _thumb_cache_total_bytes if is_thumb else _image_cache_total_bytes
        while cache and (len(cache) > max_size or current_bytes > max_bytes):
            _, (evict_image, evict_bytes) = cache.popitem(last=False)
            current_bytes -= evict_bytes
            evict_image.close()
        if is_thumb:
            _thumb_cache_total_bytes = current_bytes
        else:
            _image_cache_total_bytes = current_bytes


def _resolve_birthday_year_fallback(full_path: Path, resolved_base: Path) -> Path | None:
    try:
        rel_path = full_path.relative_to(resolved_base)
    except ValueError:
        return None

    parts = rel_path.parts
    if len(parts) < 5 or parts[:3] != ("static_images", "mysekai", "birthday"):
        return None

    directory_name = parts[3]
    if "_" not in directory_name:
        return None

    chara_name, year_text = directory_name.rsplit("_", 1)
    if not chara_name or not year_text.isdigit():
        return None

    birthday_root = resolved_base / "static_images" / "mysekai" / "birthday"
    generic_fallback = (
        resolved_base
        / "static_images"
        / "mysekai"
        / "harvest_fixture_icon"
        / "rarity_1"
        / "mdl_site_wood_common_fieldtree01.png"
    )
    if not birthday_root.is_dir():
        return generic_fallback if generic_fallback.is_file() else None

    target_year = int(year_text)
    tail_parts = parts[4:]
    fallback_candidates: list[tuple[int, Path]] = []
    for entry in birthday_root.iterdir():
        if not entry.is_dir():
            continue
        if not entry.name.startswith(chara_name + "_"):
            continue

        candidate_year = entry.name[len(chara_name) + 1 :]
        if not candidate_year.isdigit():
            continue

        candidate_path = entry.joinpath(*tail_parts)
        if candidate_path.is_file():
            fallback_candidates.append((int(candidate_year), candidate_path))

    if not fallback_candidates:
        return generic_fallback if generic_fallback.is_file() else None

    same_or_older = [item for item in fallback_candidates if item[0] <= target_year]
    if same_or_older:
        same_or_older.sort(key=lambda item: item[0], reverse=True)
        return same_or_older[0][1]

    fallback_candidates.sort(key=lambda item: item[0])
    return fallback_candidates[0][1]


def _load_image_from_path_sync(base_path: Path, path: str) -> Image.Image:
    safe_path = path.lstrip("/")
    resolved_base = base_path.resolve()
    full_path = (resolved_base / safe_path).resolve()

    if not full_path.is_relative_to(resolved_base):
        raise ValueError(f"图片路径越界: {path}")
    if not full_path.is_file():
        fallback_path = _resolve_birthday_year_fallback(full_path, resolved_base)
        if fallback_path is None:
            raise FileNotFoundError(f"图片文件不存在: {full_path}")
        full_path = fallback_path

    if not _cache_enabled(str(full_path)):
        return _open_image_copy(full_path)

    stat = full_path.stat()
    full_path_str = str(full_path)
    cached = _load_image_cached(full_path_str, stat.st_mtime_ns, stat.st_size)
    if cached is not None:
        return cached

    loaded = _open_image_copy(full_path)
    ret = loaded.copy()
    _put_image_cache(full_path_str, stat.st_mtime_ns, stat.st_size, loaded)
    return ret


def _resolve_and_stat(base_path: Path, path: str) -> tuple[Path, str, os.stat_result]:
    """解析路径并获取 stat，供 resize 和原始加载共用。"""
    safe_path = path.lstrip("/")
    resolved_base = base_path.resolve()
    full_path = (resolved_base / safe_path).resolve()

    if not full_path.is_relative_to(resolved_base):
        raise ValueError(f"图片路径越界: {path}")
    if not full_path.is_file():
        fallback_path = _resolve_birthday_year_fallback(full_path, resolved_base)
        if fallback_path is None:
            raise FileNotFoundError(f"图片文件不存在: {full_path}")
        full_path = fallback_path

    return full_path, str(full_path), full_path.stat()


def _load_image_resized_sync(
    base_path: Path,
    path: str,
    target_w: int,
    target_h: int,
    resample: int = Image.Resampling.BILINEAR,
) -> Image.Image:
    """加载图片并 resize 到目标尺寸，结果缓存。"""
    full_path, full_path_str, stat = _resolve_and_stat(base_path, path)

    if _cache_enabled(full_path_str):
        cached = _load_image_cached(full_path_str, stat.st_mtime_ns, stat.st_size, target_w, target_h)
        if cached is not None:
            return cached

    loaded = _open_image_copy(full_path)
    resized = loaded.resize((target_w, target_h), resample)
    loaded.close()

    if _cache_enabled(full_path_str):
        ret = resized.copy()
        _put_image_cache(full_path_str, stat.st_mtime_ns, stat.st_size, resized, target_w, target_h)
        return ret

    return resized


async def get_img_resized(
    base_path: Path,
    path: str | None,
    target_w: int,
    target_h: int,
    *,
    resample: int = Image.Resampling.BILINEAR,
    on_missing: MissingImageMode = "placeholder",
) -> Image.Image:
    """加载图片并 resize 到 (target_w, target_h)，利用缓存避免重复 resize。

    如果 target_w 或 target_h 为 0，则退化为 get_img_from_path（不 resize）。
    """
    if target_w <= 0 or target_h <= 0:
        return await get_img_from_path(base_path, path, on_missing)

    if path is None or path.strip() == "":
        if on_missing == "placeholder":
            _log_missing_image_once(path, "empty-path")
            img = _get_missing_placeholder_image(path)
            return img.resize((target_w, target_h), resample)
        raise ValueError("图片路径不能为空(None)")

    try:
        return await run_in_pool(
            _load_image_resized_sync, base_path, path, target_w, target_h, resample
        )
    except (FileNotFoundError, OSError) as exc:
        if on_missing == "placeholder":
            _log_missing_image_once(path, exc)
            img = _get_missing_placeholder_image(path)
            return img.resize((target_w, target_h), resample)
        raise


async def get_img_resized_long_edge(
    base_path: Path,
    path: str | None,
    long_edge: int,
    *,
    resample: int = Image.Resampling.BILINEAR,
    on_missing: MissingImageMode = "placeholder",
) -> Image.Image:
    """加载图片并按 long-edge 等比缩放，结果缓存在 _image_cache 中。

    先获取原图尺寸，计算出精确的 (target_w, target_h)，再走 get_img_resized
    的 exact-resize 缓存路径。与直接调用 resize_keep_ratio 不同，跨请求均可命中缓存。
    """
    if long_edge <= 0:
        return await get_img_from_path(base_path, path, on_missing)

    # 获取原图以得到宽高（全局缓存命中后无磁盘 I/O）
    orig = await get_img_from_path(base_path, path, on_missing=on_missing)
    orig_w, orig_h = orig.width, orig.height
    orig.close()

    # 与 resize_keep_ratio(mode="long") 逻辑一致
    if orig_w >= orig_h:
        target_w = long_edge
        target_h = max(1, int(orig_h * long_edge / orig_w))
    else:
        target_h = long_edge
        target_w = max(1, int(orig_w * long_edge / orig_h))

    return await get_img_resized(base_path, path, target_w, target_h, resample=resample, on_missing=on_missing)


def _contain_resize(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """Resize image to fit within (max_w, max_h) keeping aspect ratio (contain mode)."""
    w, h = img.size
    scale = min(max_w / w, max_h / h)
    new_w, new_h = int(w * scale), int(h * scale)
    if (new_w, new_h) == (w, h):
        return img
    return img.resize((new_w, new_h))


def _load_image_contain_resized_sync(
    base_path: Path, path: str, max_w: int, max_h: int
) -> Image.Image:
    """加载图片并 contain-resize，结果缓存（key 使用负值 max 尺寸以区分 exact resize）。"""
    full_path, full_path_str, stat = _resolve_and_stat(base_path, path)

    # 使用负值区分 contain resize 与 exact resize
    cache_tw, cache_th = -max_w, -max_h
    if _cache_enabled(full_path_str):
        cached = _load_image_cached(full_path_str, stat.st_mtime_ns, stat.st_size, cache_tw, cache_th)
        if cached is not None:
            return cached

    loaded = _open_image_copy(full_path)
    resized = _contain_resize(loaded, max_w, max_h)
    if resized is not loaded:
        loaded.close()

    if _cache_enabled(full_path_str):
        ret = resized.copy()
        _put_image_cache(full_path_str, stat.st_mtime_ns, stat.st_size, resized, cache_tw, cache_th)
        return ret

    return resized


def batch_load_and_contain_resize(
    base_path: Path,
    paths: list[str],
    max_w: int,
    max_h: int,
) -> dict[str, Image.Image]:
    """批量加载图片并 contain-resize 到 (max_w, max_h)，结果缓存。

    同步函数，设计用于 run_in_pool 中执行。
    """
    result: dict[str, Image.Image] = {}
    for path in paths:
        try:
            result[path] = _load_image_contain_resized_sync(base_path, path, max_w, max_h)
        except (FileNotFoundError, OSError):
            img = _get_missing_placeholder_image(path)
            result[path] = _contain_resize(img, max_w, max_h)
    return result


def get_str_display_length(s: str) -> int:
    """
    获取字符串的显示长度，中文字符算两个字符
    """
    length = 0
    for c in s:
        length += 1 if ord(c) < 128 else 2
    return length


def get_readable_datetime(t: datetime, show_original_time=True, use_en_unit=False):
    """
    将时间点转换为可读字符串
    """
    if not use_en_unit:
        day_unit, hour_unit, minute_unit, second_unit = ("天", "小时", "分钟", "秒")
    else:
        day_unit, hour_unit, minute_unit, second_unit = ("d", "h", "m", "s")
    now = datetime.now(t.tzinfo) if t.tzinfo is not None else datetime.now()
    diff = t - now
    text, suffix = "", "后"
    if diff.total_seconds() < 0:
        suffix = "前"
        diff = -diff
    if diff.total_seconds() < 60:
        text = f"{int(diff.total_seconds())}{second_unit}"
    elif diff.total_seconds() < 60 * 60:
        text = f"{int(diff.total_seconds() / 60)}{minute_unit}"
    elif diff.total_seconds() < 60 * 60 * 24:
        text = f"{int(diff.total_seconds() / 60 / 60)}{hour_unit}{int(diff.total_seconds() / 60 % 60)}{minute_unit}"
    else:
        text = f"{diff.days}{day_unit}"
    text += suffix
    if show_original_time:
        text = f"{t.strftime('%Y-%m-%d %H:%M:%S')} ({text})"
    return text


def truncate(s: str, limit: int) -> str:
    """
    截断字符串到指定长度，中文字符算两个字符
    """
    s = str(s)
    if s is None:
        return "<None>"
    length = 0
    for i, c in enumerate(s):
        if length >= limit:
            return s[:i] + "..."
        length += 1 if ord(c) < 128 else 2
    return s


def get_float_str(value: float, precision: int = 2) -> str:
    """格式化浮点数"""
    format_str = f"{{0:.{precision}f}}".format(value)
    if "." in format_str:
        format_str = format_str.rstrip("0").rstrip(".")
    return format_str


async def concat_images(images, direction="h"):
    """水平或垂直拼接图片"""
    if not images:
        return None

    # 过滤掉None值
    images = [img for img in images if img is not None]
    if not images:
        return None

    if direction == "h":
        # 水平拼接
        total_width = sum(img.width for img in images)
        max_height = max(img.height for img in images)

        result = Image.new("RGBA", (total_width, max_height), (0, 0, 0, 0))
        x_offset = 0
        for img in images:
            result.paste(img, (x_offset, 0))
            x_offset += img.width
    else:
        # 垂直拼接
        max_width = max(img.width for img in images)
        total_height = sum(img.height for img in images)

        result = Image.new("RGBA", (max_width, total_height), (0, 0, 0, 0))
        y_offset = 0
        for img in images:
            result.paste(img, (0, y_offset))
            y_offset += img.height

    return result


def plt_fig_to_image(fig, transparent=True) -> Image.Image:
    """
    matplot图像转换为PIL.Image对象
    """
    with io.BytesIO() as buf:
        fig.savefig(buf, transparent=transparent, format="png")
        buf.seek(0)
        with Image.open(buf) as img:
            img.load()
            return img.copy()


def get_chara_nickname(cid: int) -> str:
    return {
        1: "ick",
        2: "saki",
        3: "hnm",
        4: "shiho",
        5: "mnr",
        6: "hrk",
        7: "airi",
        8: "szk",
        9: "khn",
        10: "an",
        11: "akt",
        12: "toya",
        13: "tks",
        14: "emu",
        15: "nene",
        16: "rui",
        17: "knd",
        18: "mfy",
        19: "ena",
        20: "mzk",
        21: "miku",
        22: "rin",
        23: "len",
        24: "luka",
        25: "meiko",
        26: "kaito",
        27: "miku_light_sound",
        28: "miku_idol",
        29: "miku_street",
        30: "miku_theme_park",
        31: "miku_school_refusal",
        32: "rin",
        33: "rin",
        34: "rin",
        35: "rin",
        36: "rin",
        37: "len",
        38: "len",
        39: "len",
        40: "len",
        41: "len",
        42: "luka",
        43: "luka",
        44: "luka",
        45: "luka",
        46: "luka",
        47: "meiko",
        48: "meiko",
        49: "meiko",
        50: "meiko",
        51: "meiko",
        52: "kaito",
        53: "kaito",
        54: "kaito",
        55: "kaito",
        56: "kaito",
    }.get(cid)


# ======================= 临时文件 ======================= #

# generate music chart 使用，用于保存临时的svg图片使用浏览器截图生成png图片
# 这个路径和存放所需资源（note host和jacket）的路径都必须与那个浏览器微服务设置同一个volumes
TEMP_FILE_DIR = ASSETS_BASE_DIR / TMP_PATH
_tmp_files_to_remove: list[tuple[str, datetime]] = []
_tmp_files_lock = threading.Lock()


def cleanup_expired_tmp_files() -> int:
    """清理已过期的临时文件，返回清理的文件数量"""
    now = datetime.now()
    removed = 0
    with _tmp_files_lock:
        still_pending: list[tuple[str, datetime]] = []
        for path, expire_at in _tmp_files_to_remove:
            if now >= expire_at:
                try:
                    if os.path.exists(path):
                        os.remove(path)
                    removed += 1
                except OSError:
                    pass
            else:
                still_pending.append((path, expire_at))
        _tmp_files_to_remove.clear()
        _tmp_files_to_remove.extend(still_pending)
    return removed


def rand_filename(ext: str) -> str:
    """
    rand_filename

    生成随机的文件名

    :param ext: 文件扩展名
    :type ext: str
    :return: 随机文件名
    :rtype: str
    """
    if ext.startswith("."):
        ext = ext[1:]
    return f"{uuid4()}.{ext}"


def create_folder(folder_path) -> str:
    """
    创建文件夹，返回文件夹路径
    """
    folder_path = str(folder_path)
    os.makedirs(folder_path, exist_ok=True)
    return folder_path


def create_parent_folder(file_path) -> str:
    """
    创建文件所在的文件夹，返回文件路径
    """
    parent_folder = os.path.dirname(file_path)
    create_folder(parent_folder)
    return file_path


def remove_file(file_path):
    """
    remove_file

    删除file_path指定的文件

    :param file_path: 说明
    """
    if os.path.exists(file_path):
        os.remove(file_path)


class TempFilePath:
    """
    临时文件路径
    remove_after为None表示使用后立即删除，否则延时删除
    """

    def __init__(self, ext: str, remove_after: timedelta | None = None):
        self.ext = ext
        self.path = os.path.abspath(pjoin(TEMP_FILE_DIR, rand_filename(ext)))
        self.remove_after = remove_after
        create_parent_folder(self.path)

    def __enter__(self) -> str:
        return self.path

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.remove_after is None:
            remove_file(self.path)
        else:
            with _tmp_files_lock:
                _tmp_files_to_remove.append((self.path, datetime.now() + self.remove_after))


# ============================ 异步和任务 ============================ #

from concurrent.futures import ThreadPoolExecutor

_default_pool_executor = ThreadPoolExecutor(max_workers=DEFAULT_THREAD_POOL_SIZE)


async def run_in_pool(func, *args, pool=None):
    if pool is None:
        global _default_pool_executor
        pool = _default_pool_executor
    return await asyncio.get_running_loop().run_in_executor(pool, func, *args)


def shutdown_utils() -> None:
    """关闭 utils 模块持有的全局资源（线程池、图片缓存、临时文件）"""
    global _image_cache_total_bytes, _thumb_cache_total_bytes

    _default_pool_executor.shutdown(wait=False)

    cleanup_expired_tmp_files()

    with _image_cache_lock:
        for img, _ in _image_cache.values():
            img.close()
        _image_cache.clear()
        _image_cache_total_bytes = 0

    with _thumb_cache_lock:
        for img, _ in _thumb_cache.values():
            img.close()
        _thumb_cache.clear()
        _thumb_cache_total_bytes = 0

    with _missing_placeholder_lock:
        for img in _missing_placeholder_cache.values():
            img.close()
        _missing_placeholder_cache.clear()
        _missing_placeholder_logged.clear()


# ============================ chromedp截图 ============================ #


async def screenshot(
    url: str,
    *,
    width: int = 1920,
    height: int = 1080,
    format: Literal["png", "jpeg", "webp"] = "png",
    quanlity: int = 90,
    wait_time: int = 0,
    wait_for: str | None = None,
    full_page: bool = False,
    headers: dict | None = None,
    user_agent: str | None = None,
    device_scale: float = 1.0,
    mobile: bool = False,
    landscape: bool = False,
    req_timeout: int = 30,
    clip: dict[Literal["x", "y", "width", "height"], float] | None = None,
) -> Image.Image:
    r"""screenshot

    调用chromedp截图微服务

    Args
    ----
    url : str
        资源连接，如果是本地资源，请使用file://+绝对路径，并且保证该路径被挂载到微服务的volumes下
    width : int = 1920
        窗口宽度
    height : int = 1080
        窗口高度
    format : Literal[ 'png', 'jpeg', 'webp' ] = 'png'
        返回的截图格式
    quanlity : int = 90
        压缩质量(1 - 100)
    wait_time : int = 0
        额外等待时间(毫秒)
    wait_for : Optional[ str ] = None
        等待元素出现(CSS选择器)
    full_page : bool = False
        全页面截图
    headers : Optional[ dict ] = None
        自定义请求头
    user_agent : Optional[ str ] = None
        自定义User-Agent
    device_scale : float = 1.0
        设备像素比
    mobile : bool = false
        移动端模拟
    landscape : bool = false
        横屏模式
    timeout : int = 30
        超时时间(秒, 最大120)
    clip : Optional[ dict[ Literal[ 'x', 'y', 'width', 'height' ], float ] ] = None
        裁剪区域
    """
    # locals() 获取当前所有的局部变量，在函数开头调用，获取所有的参数
    params = {k: v for k, v in locals().items() if v is not None}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.request("post", SCREENSHOT_API_PATH, json=params) as resp:
                if resp.status != 200:
                    try:
                        error = await resp.json()
                        error = error["error"]
                    except Exception:
                        error = await resp.text
                    raise Exception(error)
                if resp.content_type not in ("image/jpeg", "image/webp", "image/png"):
                    raise Exception(f"未知的响应体类型{resp.content_type}")
                with Image.open(io.BytesIO(await resp.read())) as img:
                    img.load()
                    return img.copy()
    except aiohttp.ClientConnectionError:
        raise Exception("连接截图API失败")
