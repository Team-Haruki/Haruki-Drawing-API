from collections import OrderedDict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
import glob
import hashlib
from io import BytesIO
import logging
import math
import os
from pathlib import PurePath
import threading
from typing import Any, Literal, Self
from urllib.request import Request, urlopen

import emoji
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from PIL.ImageFont import ImageFont as Font
from pilmoji import Pilmoji, getsize as getsize_emoji
from pilmoji.source import BaseSource, GoogleEmojiSource

from src.settings import (
    DEFAULT_BOLD_FONT,  # noqa: F401
    DEFAULT_EMOJI_FONT,  # noqa: F401
    DEFAULT_FONT,  # noqa: F401
    DEFAULT_HEAVY_FONT,  # noqa: F401
    FONT_DIR,
)

from .img_utils import adjust_image_alpha_inplace
from .triangle_bg import background_hour, build_triangle_bg, gradient_points
from .utils import AssetImageRef, ImageSource, resolve_image_source_sync, run_in_pool


def shutdown_painter() -> None:
    """关闭 painter 模块持有的全局资源（磁盘缓存清理）"""
    Painter.cleanup_old_disk_cache()


DEBUG = True


def debug_print(*args, **kwargs) -> None:
    if DEBUG:
        logging.debug(*args, **kwargs)


def get_memo_usage() -> float | int:
    if DEBUG:
        import psutil

        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        return mem_info.rss / (1024 * 1024)  # 返回单位为MB
    return 0


def deterministic_hash(obj: Any) -> str:
    """
    计算复杂对象的确定性哈希值
    """
    ret = hashlib.md5()

    def update(s: str | bytes) -> None:
        if isinstance(s, str):
            s = s.encode("utf-8")
        ret.update(s)

    def _serialize(_obj: Any):
        # 基本类型
        if _obj is None:
            update(b"None")
            return None
        elif isinstance(_obj, bool):
            update(str(_obj))
            return None
        elif isinstance(_obj, int):
            update(str(_obj))
            return None
        elif isinstance(_obj, float):
            update(str(_obj))
            return None
        elif isinstance(_obj, str):
            update(str(_obj))
            return None
        elif isinstance(_obj, bytes):
            update(_obj)
            return None
        elif isinstance(_obj, PurePath):
            update(str(_obj))
            return None

        # 容器类型
        elif isinstance(_obj, list | tuple):
            for item in _obj:
                _serialize(item)

        elif isinstance(_obj, dict):
            # 字典按键排序确保一致性
            for key, value in sorted(_obj.items()):
                _serialize(key)
                _serialize(value)

        elif isinstance(_obj, set):
            # 集合元素排序确保一致性
            for item in sorted(_obj):
                _serialize(item)

        elif isinstance(_obj, frozenset):
            for item in sorted(_obj):
                _serialize(item)

        # PIL Image
        elif isinstance(_obj, Image.Image):
            _serialize_pil_image(_obj)
            return None

        # NumPy数组
        elif isinstance(_obj, np.ndarray):
            _serialize_numpy_array(_obj)
            return None

        # Dataclass
        elif is_dataclass(_obj) and not isinstance(_obj, type):
            _serialize_dataclass(_obj)
            return None

        # 有__dict__属性的自定义对象
        elif hasattr(_obj, "__dict__"):
            class_name = f"{_obj.__class__.__module__}.{_obj.__class__.__name__}"
            dict_data = {k: v for k, v in _obj.__dict__.items() if not k.startswith("_")}
            update(f"object:{class_name}:")
            _serialize(dict_data)
            return None

        # 其他可迭代对象
        elif hasattr(_obj, "__iter__") and not isinstance(_obj, str | bytes):
            update(f"iterable:{type(_obj).__name__}:")
            for item in _obj:
                _serialize(item)

        else:
            # 其他类型的对象
            try:
                class_name = f"{_obj.__class__.__module__}.{_obj.__class__.__name__}"
                update(f"{class_name}:")
                attrs = dir(_obj)
                for attr in attrs:
                    if not attr.startswith("_"):
                        value = getattr(_obj, attr)
                        _serialize(value)
            except Exception:
                return f"fallback:{type(_obj).__name__}:{id(_obj)}"

    def _serialize_pil_image(img: Image.Image):
        """序列化PIL Image"""
        update(f"{img.size[0]}x{img.size[1]}:{img.mode}:")
        update(img.tobytes())

    def _serialize_numpy_array(arr):
        """序列化NumPy数组"""
        arr_bytes = arr.tobytes()
        arr_shape = arr.shape
        arr_dtype = arr.dtype.str
        update(f"{arr_shape}:{arr_dtype}:")
        update(arr_bytes)

    def _serialize_dataclass(_obj):
        """序列化dataclass对象"""
        class_name = f"{_obj.__class__.__module__}.{_obj.__class__.__name__}"
        update(f"{class_name}:")
        # 获取所有字段
        for _field in fields(_obj):
            field_value = getattr(_obj, _field.name)
            update(f"{_field.name}:")
            _serialize(field_value)

    _serialize(obj)
    return ret.hexdigest()


# =========================== 基础定义 =========================== #

PAINTER_CACHE_DIR = "data/utils/painter_cache/"
PAINTER_EMOJI_CACHE_DIR = "data/utils/painter_emoji_cache/"
PAINTER_EMOJI_CACHE_MAX_ENTRIES = 512
PAINTER_EMOJI_SOURCE_TIMEOUT_SECONDS = 3
_painter_emoji_cache_lock = threading.RLock()
_painter_emoji_bytes_cache: OrderedDict[str, bytes] = OrderedDict()

Color = tuple[int, int, int, int] | tuple[int, int, int] | list[int]
Position = tuple[int, int]
Size = tuple[int, int]

BLACK = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)
RED = (255, 0, 0, 255)
GREEN = (0, 255, 0, 255)
BLUE = (0, 0, 255, 255)
TRANSPARENT = (0, 0, 0, 0)
SHADOW = (0, 0, 0, 150)

ROUNDRECT_ANTIALIASING_TARGET_RADIUS = 16

ALIGN_MAP = {
    "c": ("c", "c"),
    "l": ("l", "c"),
    "r": ("r", "c"),
    "t": ("c", "t"),
    "b": ("c", "b"),
    "tl": ("l", "t"),
    "tr": ("r", "t"),
    "bl": ("l", "b"),
    "br": ("r", "b"),
    "lt": ("l", "t"),
    "lb": ("l", "b"),
    "rt": ("r", "t"),
    "rb": ("r", "b"),
}
ALIGN_TYPE = Literal[
    "c",
    "l",
    "r",
    "t",
    "b",
    "tl",
    "tr",
    "bl",
    "br",
    "lt",
    "lb",
    "rt",
    "rb",
]

ITEM_SIZE_MODE_TYPE = Literal["expand", "fixed"]

# =========================== 工具函数 =========================== #


@dataclass
class FontDesc:
    path: str
    size: int


@dataclass
class FontCacheEntry:
    font: Font
    last_used: datetime


FONT_CACHE_MAX_NUM = 32
_font_cache_local = threading.local()
_painter_disk_cache_lock = threading.RLock()


def _get_thread_font_cache() -> dict[str, FontCacheEntry]:
    cache = getattr(_font_cache_local, "font_cache", None)
    if cache is None:
        cache = {}
        _font_cache_local.font_cache = cache
    return cache


def crop_by_align(original_size: int, crop_size: int, align: int) -> tuple[int, int, int, int]:
    w, h = original_size
    cw, ch = crop_size
    assert cw <= w, "Crop width must be smaller than original width"
    assert ch <= h, "Crop height must be smaller than original height"
    x, y = 0, 0
    xa, ya = ALIGN_MAP[align]
    if xa == "l":
        x = 0
    elif xa == "r":
        x = w - cw
    elif xa == "c":
        x = (w - cw) // 2
    if ya == "t":
        y = 0
    elif ya == "b":
        y = h - ch
    elif ya == "c":
        y = (h - ch) // 2
    return x, y, x + cw, y + ch


def color_code_to_rgb(code: str) -> Color:
    if code.startswith("#"):
        code = code[1:]
    if len(code) == 3:
        return int(code[0], 16) * 16, int(code[1], 16) * 16, int(code[2], 16) * 16, 255
    elif len(code) == 6:
        return int(code[0:2], 16), int(code[2:4], 16), int(code[4:6], 16), 255
    raise ValueError("Invalid color code")


def rgb_to_color_code(rgb: Color) -> str:
    r, g, b = rgb[:3]
    return f"#{r:02x}{g:02x}{b:02x}"


def lerp_color(c1: list[int] | tuple[int, ...], c2: list[int] | tuple[int, ...], t: float) -> tuple[int, ...]:
    ret = []
    for i in range(len(c1)):
        ret.append(max(0, min(255, int(c1[i] * (1 - t) + c2[i] * t))))
    return tuple(ret)


def adjust_color(
    c: list[int] | tuple[int, ...],
    r: int | None = None,
    g: int | None = None,
    b: int | None = None,
    a: int | None = None,
) -> tuple[int, int, int, int]:
    c = list(c)
    if len(c) == 3:
        c.append(255)
    if r is not None:
        c[0] = r
    if g is not None:
        c[1] = g
    if b is not None:
        c[2] = b
    if a is not None:
        c[3] = a
    return c[0], c[1], c[2], c[3]


def get_font_desc(path: str, size: int) -> FontDesc:
    return FontDesc(path=path, size=size)


def get_font(path: str, size: int) -> Font:
    key = f"{path}_{size}"
    paths = [
        path,
        os.path.join(FONT_DIR, path),
        os.path.join(FONT_DIR, path + ".otf"),
        os.path.join(FONT_DIR, path + ".ttf"),
        os.path.join(FONT_DIR, path + ".ttc"),
    ]
    font_cache = _get_thread_font_cache()
    entry = font_cache.get(key)
    if entry is None:
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont | None = None
        for font_path in paths:
            if os.path.exists(font_path):
                font = ImageFont.truetype(font_path, size)
                break
        if font is None:
            font = ImageFont.load_default()
        entry = FontCacheEntry(font, datetime.now())
        font_cache[key] = entry
        # 清理当前线程中过期的字体缓存
        while len(font_cache) > FONT_CACHE_MAX_NUM:
            oldest_key = min(font_cache, key=lambda k: font_cache[k].last_used)
            del font_cache[oldest_key]
    else:
        entry.last_used = datetime.now()
    return entry.font


# Text measurement dominates the render. Profiling one inventory/list request: Font.getsize was
# 6816 calls / 0.97s — 84% of the 1.15s it takes to walk the widget tree, and 7x the entire native
# Skia render (0.16s). 518 text nodes measured ~13 times each: every node re-measures the "哇"
# standard box for its baseline, and layout measures a string again at draw time.
#
# Measuring is a PURE function of (face, size, string), so cache it. The cache is keyed by the font
# FILE and size, not the font object, so all pool threads share the results while each keeps its own
# FreeTypeFont (sharing the object would serialize every measurement — see ir_builder's font cache).
_TEXT_BBOX_CACHE_MAX = 50_000
_text_bbox_cache: dict[tuple, tuple[int, int, int, int]] = {}
_text_emoji_size_cache: dict[tuple, Size] = {}


def _font_key(font: Font) -> tuple:
    """A cross-thread identity for a font: its file + size. Falls back to the object id for PIL's
    in-memory default face, which has no path."""
    path = getattr(font, "path", None)
    size = getattr(font, "size", None)
    return (path, size) if isinstance(path, str) else (id(font), size)


def _measure_bbox(font: Font, text: str) -> tuple[int, int, int, int]:
    key = (_font_key(font), text)
    cached = _text_bbox_cache.get(key)
    if cached is not None:
        return cached
    bbox = font.getbbox(text)
    # A plain dict is enough: entries are immutable, a duplicate compute under a race is harmless,
    # and a lock here would re-serialize the very thing this cache exists to parallelize. The bound
    # matters because the keys carry request text; a wholesale clear is fine since a cold measure is
    # cheap and correctness never depends on a hit.
    if len(_text_bbox_cache) >= _TEXT_BBOX_CACHE_MAX:
        _text_bbox_cache.clear()
    _text_bbox_cache[key] = bbox
    return bbox


def get_text_size(font: Font, text: str) -> Size:
    if emoji.emoji_count(text) > 0:
        key = (_font_key(font), text)
        cached = _text_emoji_size_cache.get(key)
        if cached is None:
            cached = getsize_emoji(text, font=font)
            if len(_text_emoji_size_cache) >= _TEXT_BBOX_CACHE_MAX:
                _text_emoji_size_cache.clear()
            _text_emoji_size_cache[key] = cached
        return cached
    bbox = _measure_bbox(font, text)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def get_text_offset(font: Font, text: str) -> Position:
    bbox = _measure_bbox(font, text)
    return bbox[0], bbox[1]


def ascender_top_to_painter_y(font_path: str, font_size: int, ascender_top_y: int) -> int:
    """Convert an ``ImageDraw.text`` y (its default ``"la"`` anchor = top of the ascender)
    into the y ``Painter.text`` expects (it anchors the baseline at ``y + ink-height("哇")``).

    The two differ by ``ascent - ink_height("哇")`` — 4px for the bold font at size 20 — so a
    layout constant lifted straight from the old ImageDraw code lands the text that much too
    high. The gap is font- and size-dependent, so derive it from the metrics rather than
    folding a fudge factor into the constant. Both backends agree: the Skia path resolves
    ``Painter.text``'s logical top through the same Pillow ink height (IRBuilder's
    ``cjk_top`` baseline)."""
    font = get_font(font_path, font_size)
    return ascender_top_y + font.getmetrics()[0] - get_text_size(font, "哇")[1]


def resize_keep_ratio(img: Image.Image, max_size: float, mode: str = "long", scale: int | None = None) -> Image.Image:
    """
    Resize image to keep the aspect ratio, with a maximum size.
    mode in ['long', 'short', 'w', 'h', 'wxh', 'scale']
    """
    w, h = img.size
    if mode == "long":
        if w > h:
            ratio = max_size / w
        else:
            ratio = max_size / h
    elif mode == "short":
        if w > h:
            ratio = max_size / h
        else:
            ratio = max_size / w
    elif mode == "w":
        ratio = max_size / w
    elif mode == "h":
        ratio = max_size / h
    elif mode == "wxh":
        ratio = math.sqrt(max_size / (w * h))
    elif mode == "scale":
        ratio = max_size
    else:
        raise ValueError(f"Invalid mode: {mode}")
    if scale:
        ratio *= scale
    return img.resize((int(w * ratio), int(h * ratio)), Image.Resampling.BILINEAR)


def resize_by_optional_size(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    if size[0] is None and size[1] is None:
        return img
    if size[0] is None:
        if img.size[1] == size[1]:
            return img
        return resize_keep_ratio(img, size[1], mode="h")
    if size[1] is None:
        if img.size[0] == size[0]:
            return img
        return resize_keep_ratio(img, size[0], mode="w")
    if img.size[0] == size[0] and img.size[1] == size[1]:
        return img
    return img.resize(size, Image.Resampling.BILINEAR)


class Gradient:
    def get_colors(self, size: Size) -> np.ndarray:
        # [W, H, 4]
        raise NotImplementedError()

    def get_img(self, size: Size, mask: Image.Image = None) -> Image.Image:
        colors = self.get_colors(size)
        mode = "RGBA" if colors.shape[-1] == 4 else "RGB"
        img = Image.fromarray(colors, mode)
        if mode == "RGB":
            img = img.convert("RGBA")
        if mask:
            assert mask.size == size, "Mask size must match image size"
            if mask.mode == "RGBA":
                mask = mask.split()[3]
            else:
                mask = mask.convert("L")
            img.putalpha(mask)
        return img


class LinearGradient(Gradient):
    def __init__(self, c1: Color, c2: Color, p1: Position, p2: Position, method: str = "combine") -> None:
        self.c1 = c1
        self.c2 = c2
        self.p1 = p1
        self.p2 = p2
        self.method = method
        assert p1 != p2, "p1 and p2 cannot be the same point"
        assert method in ("combine", "separate")

    def get_colors(self, size: Size) -> np.ndarray:
        w, h = size
        pixel_p1 = np.array((self.p1[1] * h, self.p1[0] * w))
        pixel_p2 = np.array((self.p2[1] * h, self.p2[0] * w))
        y_indices, x_indices = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
        coords = np.stack((y_indices, x_indices), axis=-1)  # (H, W, 2)
        t = None
        if self.method == "combine":
            gradient_vector = pixel_p2 - pixel_p1
            length_sq = np.sum(gradient_vector**2)
            vector_p1_to_pixel = coords - pixel_p1  # (H, W, 2)
            dot_product = np.sum(vector_p1_to_pixel * gradient_vector, axis=-1)  # (H, W)
            t = dot_product / length_sq
        elif self.method == "separate":
            vector_pixel_to_p1 = coords - pixel_p1
            vector_p2_to_p1 = pixel_p2 - pixel_p1
            # 避免除以0
            denom = np.where(vector_p2_to_p1 == 0, 1e-9, vector_p2_to_p1)
            t_dims = vector_pixel_to_p1 / denom
            # 如果某维度位移为0，则该维度的比例不应参与平均（或者设为0）
            t = np.sum(np.where(vector_p2_to_p1 == 0, 0, t_dims), axis=-1) / np.sum(vector_p2_to_p1 != 0)
        assert t is not None
        t_clamped = np.clip(t, 0, 1)
        colors = (1 - t_clamped[:, :, np.newaxis]) * self.c1 + t_clamped[:, :, np.newaxis] * self.c2
        colors = np.clip(colors, 0, 255).astype(np.uint8)
        return colors


class RadialGradient(Gradient):
    def __init__(self, c1: Color, c2: Color, center: Position, radius: float) -> None:
        self.c1 = c1
        self.c2 = c2
        self.center = center
        self.radius = radius

    def get_colors(self, size: Size) -> np.ndarray:
        w, h = size
        center = np.array(self.center) * np.array((w, h))
        y_indices, x_indices = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
        coords = np.stack((x_indices, y_indices), axis=-1)
        dist = np.linalg.norm(coords - center, axis=-1) / self.radius
        dist = np.clip(dist, 0, 1)
        colors = dist[:, :, np.newaxis] * np.array(self.c1) + (1 - dist)[:, :, np.newaxis] * np.array(self.c2)
        return colors.astype(np.uint8)


@dataclass
class AdaptiveTextColor:
    pixelwise: bool = False
    light: Color = WHITE
    dark: Color = BLACK
    threshold: float = 0.4


ADAPTIVE_WB = AdaptiveTextColor()
ADAPTIVE_SHADOW = AdaptiveTextColor(
    light=(255, 255, 255, 100),
    dark=(0, 0, 0, 100),
)


# =========================== 绘图类 =========================== #


@dataclass
class PainterOperation:
    offset: Position
    size: Size
    func: str | Callable
    args: list
    exclude_on_hash: bool


def _resolve_paste_source(source: ImageSource, size) -> Image.Image:
    """Resolve a lazy paste source to pixels. An ``AssetImageRef`` with a concrete
    target size goes through the global resize cache, so layers shared across many
    widgets (rarity stars, card frames, attr icons) resize once per size."""
    if isinstance(source, Image.Image):
        return source
    if isinstance(source, AssetImageRef) and size and size[0] and size[1] and tuple(size) != tuple(source.size):
        return resolve_image_source_sync(source, target_size=(int(size[0]), int(size[1])))
    return resolve_image_source_sync(source)


def _emoji_source_cache_key(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"google_{kind}_{digest}"


def _emoji_source_cache_path(cache_key: str) -> str:
    return os.path.join(PAINTER_EMOJI_CACHE_DIR, f"{cache_key}.bin")


def _get_emoji_source_bytes_cached(cache_key: str) -> bytes | None:
    with _painter_emoji_cache_lock:
        cached = _painter_emoji_bytes_cache.get(cache_key)
        if cached is not None:
            _painter_emoji_bytes_cache.move_to_end(cache_key)
            return cached

    path = _emoji_source_cache_path(cache_key)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return None
    except OSError:
        logging.debug("Failed to read painter emoji cache: %s", path, exc_info=True)
        return None

    if not data:
        return None

    with _painter_emoji_cache_lock:
        _painter_emoji_bytes_cache[cache_key] = data
        _painter_emoji_bytes_cache.move_to_end(cache_key)
        while len(_painter_emoji_bytes_cache) > PAINTER_EMOJI_CACHE_MAX_ENTRIES:
            _painter_emoji_bytes_cache.popitem(last=False)
    return data


def _put_emoji_source_bytes_cached(cache_key: str, data: bytes) -> None:
    if not data:
        return

    with _painter_emoji_cache_lock:
        _painter_emoji_bytes_cache[cache_key] = data
        _painter_emoji_bytes_cache.move_to_end(cache_key)
        while len(_painter_emoji_bytes_cache) > PAINTER_EMOJI_CACHE_MAX_ENTRIES:
            _painter_emoji_bytes_cache.popitem(last=False)

    path = _emoji_source_cache_path(cache_key)
    tmp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        os.makedirs(PAINTER_EMOJI_CACHE_DIR, exist_ok=True)
        with open(tmp_path, "wb") as f:
            f.write(data)
        os.replace(tmp_path, path)
    except OSError:
        logging.debug("Failed to write painter emoji cache: %s", path, exc_info=True)
        with suppress(OSError):
            os.remove(tmp_path)


class _TimeoutGoogleEmojiSource(GoogleEmojiSource):
    def request(self, url: str) -> bytes:
        session = getattr(self, "_requests_session", None)
        if session is not None:
            with session.get(url, **self.REQUEST_KWARGS, timeout=PAINTER_EMOJI_SOURCE_TIMEOUT_SECONDS) as response:
                if response.ok:
                    return response.content
                response.raise_for_status()

        req = Request(url, **self.REQUEST_KWARGS)
        with urlopen(req, timeout=PAINTER_EMOJI_SOURCE_TIMEOUT_SECONDS) as response:
            return response.read()


class CachedGoogleEmojiSource(BaseSource):
    def _get_cached_stream(self, kind: str, value: str, fetch: Callable[[_TimeoutGoogleEmojiSource], BytesIO | None]):
        cache_key = _emoji_source_cache_key(kind, value)
        cached = _get_emoji_source_bytes_cached(cache_key)
        if cached is not None:
            return BytesIO(cached)

        source = _TimeoutGoogleEmojiSource()
        stream = None
        try:
            stream = fetch(source)
            if stream is None:
                return None
            data = stream.getvalue()
        except Exception:
            logging.debug("Failed to fetch painter emoji source: kind=%s value=%s", kind, value, exc_info=True)
            return None
        finally:
            if stream is not None:
                stream.close()
            session = getattr(source, "_requests_session", None)
            if session is not None:
                session.close()

        _put_emoji_source_bytes_cached(cache_key, data)
        return BytesIO(data)

    def get_emoji(self, emoji: str, /) -> BytesIO | None:
        return self._get_cached_stream("emoji", emoji, lambda source: source.get_emoji(emoji))

    def get_discord_emoji(self, id: int, /) -> BytesIO | None:
        return self._get_cached_stream("discord", str(id), lambda source: source.get_discord_emoji(id))


class Painter:
    def __init__(self, img: Image.Image | None = None, size: tuple[int, int] | None = None) -> None:
        self.operations: list[PainterOperation] = []
        if img is not None:
            self.img = img
            self.size = img.size
        elif size is not None:
            self.img = None
            self.size = size
        else:
            raise ValueError("Either img or size must be provided")
        self.offset = (0, 0)
        self.w = self.size[0]
        self.h = self.size[1]
        self.region_stack = []
        # Layer frames for push_clip_roundrect/push_mask: (kind, saved_img, pos_in_parent,
        # size, payload) where payload is (radius, corners) for a roundrect clip and the mask
        # ImageSource for a mask. While a layer is open, self.img is a layer-rect-sized buffer
        # and _execute rebases every op offset by _clip_origin (the buffer origin in canvas
        # coords).
        self._clip_stack: list[tuple[str, Image.Image, tuple[int, int], tuple[int, int], Any]] = []
        self._clip_origin = (0, 0)

    def _text(self, text: str, pos: Position, font: Font, fill: Color = BLACK, align: str = "left") -> Self:
        std_size = get_text_size(font, "哇")
        has_emoji = emoji.emoji_count(text) > 0
        if not has_emoji:
            draw = ImageDraw.Draw(self.img)
            text_offset = (0, -std_size[1])
            pos = (pos[0] - text_offset[0] + self.offset[0], pos[1] - text_offset[1] + self.offset[1])
            draw.text(pos, text, font=font, fill=fill, align=align, anchor="ls")
        else:
            with Pilmoji(self.img, source=CachedGoogleEmojiSource) as pilmoji:
                text_offset = (0, -std_size[1])
                pos = (pos[0] - text_offset[0] + self.offset[0], pos[1] - text_offset[1] + self.offset[1])
                pilmoji.text(
                    pos,
                    text,
                    font=font,
                    fill=fill,
                    align=align,
                    emoji_position_offset=(0, 0),
                    anchor="ls",
                )
        return self

    @staticmethod
    def _execute(operations: list[PainterOperation], img: Image.Image, size: tuple[int, int]) -> Image.Image:
        t = datetime.now()
        if img is None:
            img = Image.new("RGBA", size, TRANSPARENT)
        p = Painter(img, size)
        for op in operations:
            p.offset = (op.offset[0] - p._clip_origin[0], op.offset[1] - p._clip_origin[1])
            p.size = op.size
            p.w, p.h = op.size
            func = getattr(p, op.func) if isinstance(op.func, str) else op.func
            func(*op.args)
        assert not p._clip_stack, "unbalanced push_clip_roundrect/push_mask"
        debug_print(f"Painter._execute use time: {datetime.now() - t}")
        return p.img

    async def get(self, cache_key: str | None = None) -> Image.Image:
        # 使用缓存
        if cache_key is not None:
            t = datetime.now()
            debug_print(f"Cache key: {cache_key}")
            op_hash = await run_in_pool(deterministic_hash, {"key": cache_key, "op": self.operations})
            debug_print(f"Cache key: {cache_key}, op_hash: {op_hash}, elapsed: {datetime.now() - t}")

            with _painter_disk_cache_lock:
                paths = glob.glob(os.path.join(PAINTER_CACHE_DIR, f"{cache_key}__*.png"))
                if paths:
                    path = paths[0]
                    if path.endswith(f"{cache_key}__{op_hash}.png"):
                        # 如果hash相同则直接返回缓存的图片
                        debug_print(f"Using cached image: {path}")
                        with Image.open(path) as img:
                            img.load()
                            return img.copy()
                    else:
                        # 否则清空缓存并重新绘图
                        for p in paths:
                            try:
                                os.remove(p)
                            except Exception as e:
                                logging.warning(f"Failed to remove cache file {p}: {e}")
                        debug_print(f"Cache mismatch, removed {len(paths)} files")

        debug_print(f"Memory usage: {get_memo_usage()} MB")

        try:
            for op in self.operations:
                debug_print(str(op))

            # 执行绘图操作
            t = datetime.now()
            self.img = await run_in_pool(Painter._execute, self.operations, self.img, self.size)
            debug_print(f"Painter executed in thread pool in {datetime.now() - t}")
        finally:
            self.operations = []

        # 保存缓存
        if cache_key is not None:
            try:
                with _painter_disk_cache_lock:
                    cache_path = os.path.join(PAINTER_CACHE_DIR, f"{cache_key}__{op_hash}.png")
                    os.makedirs(PAINTER_CACHE_DIR, exist_ok=True)
                    self.img.save(cache_path, format="PNG")
            except Exception:
                debug_print(f"Failed to save cache for {cache_key}")

        return self.img

    def add_operation(self, func: str | Callable, exclude_on_hash: bool, args: Any):
        self.operations.append(
            PainterOperation(
                offset=self.offset,
                size=self.size,
                func=func,
                args=list(args),
                exclude_on_hash=exclude_on_hash,
            )
        )
        return self

    @staticmethod
    def clear_cache(cache_key: str) -> int:
        with _painter_disk_cache_lock:
            paths = glob.glob(os.path.join(PAINTER_CACHE_DIR, f"{cache_key}__*.png"))
            ok = 0
            for p in paths:
                try:
                    os.remove(p)
                    ok += 1
                except Exception as e:
                    logging.warning(f"Failed to remove cache file {p}: {e}")
            return ok

    @staticmethod
    def get_cache_key_mtimes() -> dict[str, datetime]:
        with _painter_disk_cache_lock:
            paths = glob.glob(os.path.join(PAINTER_CACHE_DIR, "*.png"))
            cache_keys = {}
            for p in paths:
                mtime = os.path.getmtime(p)
                cache_key = os.path.basename(p).split("__")[0]
                cache_keys[cache_key] = datetime.fromtimestamp(mtime)
            return cache_keys

    @staticmethod
    def cleanup_old_disk_cache(max_age_days: int = 7) -> int:
        """删除超过 max_age_days 天未修改的磁盘缓存文件，返回删除数量"""
        import time

        cutoff = time.time() - max_age_days * 86400
        removed = 0
        with _painter_disk_cache_lock:
            for p in glob.glob(os.path.join(PAINTER_CACHE_DIR, "*.png")):
                try:
                    if os.path.getmtime(p) < cutoff:
                        os.remove(p)
                        removed += 1
                except OSError:
                    pass
        return removed

    def set_region(self, pos: Position, size: Size) -> Self:
        assert isinstance(pos[0], int), "Position x must be integer"
        assert isinstance(pos[1], int), "Position y must be integer"
        assert isinstance(size[0], int), "Size width must be integer"
        assert isinstance(size[1], int), "Size height must be integer"
        self.region_stack.append((self.offset, self.size))
        self.offset = pos
        self.size = size
        self.w = size[0]
        self.h = size[1]
        return self

    def shrink_region(self, dlt: Position) -> Self:
        pos = (self.offset[0] + dlt[0], self.offset[1] + dlt[1])
        size = (self.size[0] - dlt[0] * 2, self.size[1] - dlt[1] * 2)
        return self.set_region(pos, size)

    def expand_region(self, dlt: Position) -> Self:
        pos = (self.offset[0] - dlt[0], self.offset[1] - dlt[1])
        size = (self.size[0] + dlt[0] * 2, self.size[1] + dlt[1] * 2)
        return self.set_region(pos, size)

    def move_region(self, dlt: Position, size: Size = None) -> Self:
        offset = (self.offset[0] + dlt[0], self.offset[1] + dlt[1])
        size = size or self.size
        return self.set_region(offset, size)

    def restore_region(self, depth=1) -> Self:
        if not self.region_stack:
            self.offset = (0, 0)
            self.size = self.img.size
            self.w = self.img.size[0]
            self.h = self.img.size[1]
        else:
            self.offset, self.size = self.region_stack.pop()
            self.w = self.size[0]
            self.h = self.size[1]
        if depth > 1:
            return self.restore_region(depth - 1)
        return self

    def text(
        self,
        text: str,
        pos: Position,
        font: FontDesc | Font,
        fill: Color | LinearGradient | AdaptiveTextColor = BLACK,
        align: str = "left",
        exclude_on_hash: bool = False,
    ) -> Self:
        """
        绘制文本

        Parameters:
            text: 要绘制的单行文本内容
            pos: 文本位置 (x, y)
            font: 字体，可以是FontDesc或PIL ImageFont对象
            fill: 填充颜色，可以是Color/LinearGradient/AdaptiveTextColor
            align: 对齐方式，'left', 'center', 'right'
            exclude_on_hash: 是否在哈希计算中排除此操作
        """
        return self.add_operation("_impl_text", exclude_on_hash, (text, pos, font, fill, align))

    def paste(
        self,
        sub_img: ImageSource,
        pos: Position,
        size: Size = None,
        use_shadow: bool = False,
        shadow_width: int = 8,
        shadow_alpha: float = 0.6,
        src_rect: tuple[float, float, float, float] | None = None,
        exclude_on_hash: bool = False,
    ) -> Self:
        """``src_rect`` crops the source to ``(x0, y0, x1, y1)`` in source pixels before
        the fit (both backends; the IR Image node's ``source_rect``), so slices of one
        asset draw without a Python-side crop/decode."""
        return self.add_operation(
            "_impl_paste",
            exclude_on_hash,
            (sub_img, pos, size, use_shadow, shadow_width, shadow_alpha, src_rect),
        )

    def image_bg(
        self,
        image: ImageSource,
        align: ALIGN_TYPE = "c",
        mode: Literal["fit", "fill", "fixed", "repeat"] = "fit",
        blur: bool = False,
        fade: float = 0.1,
        exclude_on_hash: bool = False,
    ) -> Self:
        """Draw an image across the current region with ``ImageBg`` semantics.

        Effects are deliberately part of the Painter operation instead of being applied by
        ``plot.ImageBg.__init__``. Pillow therefore resolves a lazy source inside its render
        worker, while IRPainter can keep an :class:`AssetImageRef` as a Rust-decoded path and
        express the same fade/blur as image decorations.
        """
        return self.add_operation("_impl_image_bg", exclude_on_hash, (image, align, mode, blur, fade))

    def paste_with_alpha_blend(
        self,
        sub_img: ImageSource,
        pos: Position,
        size: Size = None,
        alpha: float | None = None,
        use_shadow: bool = False,
        shadow_width: int = 8,
        shadow_alpha: float = 0.6,
        src_rect: tuple[float, float, float, float] | None = None,
        exclude_on_hash: bool = False,
    ) -> Self:
        return self.add_operation(
            "_impl_paste_with_alpha_blend",
            exclude_on_hash,
            (sub_img, pos, size, alpha, use_shadow, shadow_width, shadow_alpha, src_rect),
        )

    def paste_src(
        self,
        sub_img: ImageSource,
        pos: Position,
        size: Size = None,
        src_rect: tuple[float, float, float, float] | None = None,
        exclude_on_hash: bool = False,
    ) -> Self:
        """Porter-Duff **Src**: write all four channels verbatim, replacing the destination
        (Pillow: a mask-less ``Image.paste``). For the BASE layer of an absolute-coordinate
        composite — the asset that *is* the canvas — where the alternatives both lose data:

        - :meth:`paste` lerps the destination toward the source, which over an empty canvas
          squares the alpha of an anti-aliased edge;
        - :meth:`paste_with_alpha_blend` (src-over) is exact wherever the result is visible, but
          zeroes the rgb UNDER fully transparent pixels — and Pillow's own paste-lerp reads that
          rgb back when a later overlay's AA edge crosses those pixels (an honor badge's frame
          over the transparent corners of its base art shifts by up to 228/255 without it).

        The Skia backend draws it src-over, which is identical wherever the destination is empty
        — the only supported use — except that a premultiplied surface cannot carry rgb under
        zero alpha at all. That is the pre-existing backend divergence, not a new one."""
        return self.add_operation("_impl_paste_src", exclude_on_hash, (sub_img, pos, size, src_rect))

    def push_clip_roundrect(
        self,
        pos: Position,
        size: Size,
        radius: float,
        corners: tuple[bool, bool, bool, bool] = (True, True, True, True),
        exclude_on_hash: bool = False,
    ) -> Self:
        """Clip subsequent draws to a rounded rect until the matching :meth:`pop_clip`.

        Pillow implements this as an offscreen buffer masked back on pop, so
        backdrop-sampling ops (blurglass, adaptive text color) inside the clip see a
        transparent backdrop; the Skia path clips on the live surface."""
        return self.add_operation("_impl_push_clip_roundrect", exclude_on_hash, (pos, size, radius, corners))

    def pop_clip(self, exclude_on_hash: bool = False) -> Self:
        return self.add_operation("_impl_pop_clip", exclude_on_hash, ())

    def push_mask(
        self,
        mask: ImageSource,
        pos: Position,
        size: Size,
        exclude_on_hash: bool = False,
    ) -> Self:
        """Mask subsequent draws with an arbitrary image's alpha until :meth:`pop_mask`.

        The ops between push/pop render into their own layer covering ``pos``/``size``; on pop
        the layer's alpha is MULTIPLIED by the ALPHA CHANNEL of ``mask`` (stretched to ``size``
        when it does not match) and the layer is composited back over the destination. That is Skia's
        ``DstIn`` and the same alpha arithmetic :meth:`push_clip_roundrect` applies with its
        rounded rect, so both backends agree.

        It reproduces Pillow's ``img.putalpha(mask.split()[3])`` whenever the layer is opaque
        where the mask is — which is what an absolute-coordinate composite over a solid
        background gives. A layer that is already translucent there stays translucent (the
        alphas multiply); ``putalpha`` would have overwritten it.

        Like the roundrect clip, this is an offscreen buffer on the Pillow side, so
        backdrop-sampling ops (blurglass, adaptive text color) inside a mask see a transparent
        backdrop."""
        return self.add_operation("_impl_push_mask", exclude_on_hash, (mask, pos, size))

    def pop_mask(self, exclude_on_hash: bool = False) -> Self:
        return self.add_operation("_impl_pop_mask", exclude_on_hash, ())

    def shadow_roundrect(
        self,
        pos: Position,
        size: Size,
        radius: float,
        shadow_width: int = 6,
        shadow_alpha: float = 0.3,
        exclude_on_hash: bool = False,
    ) -> Self:
        """Draw a blurred rounded-rect drop shadow (both backends: blurred rrect, no offset)."""
        return self.add_operation(
            "_impl_shadow_roundrect", exclude_on_hash, (pos, size, radius, shadow_width, shadow_alpha)
        )

    def rect(
        self,
        pos: Position,
        size: Size,
        fill: Color | Gradient,
        stroke: Color | None = None,
        stroke_width: int = 1,
        exclude_on_hash: bool = False,
    ) -> Self:
        return self.add_operation("_impl_rect", exclude_on_hash, (pos, size, fill, stroke, stroke_width))

    def roundrect(
        self,
        pos: Position,
        size: Size,
        fill: Color | Gradient,
        radius: int,
        stroke: Color = None,
        stroke_width: int = 1,
        corners=(True, True, True, True),
        exclude_on_hash: bool = False,
    ) -> Self:
        return self.add_operation(
            "_impl_roundrect", exclude_on_hash, (pos, size, fill, radius, stroke, stroke_width, corners)
        )

    def pieslice(
        self,
        pos: Position,
        size: Size,
        start_angle: float,
        end_angle: float,
        fill: Color,
        stroke: Color = None,
        stroke_width: int = 1,
        exclude_on_hash: bool = False,
    ) -> Self:
        return self.add_operation(
            "_impl_pieslice", exclude_on_hash, (pos, size, start_angle, end_angle, fill, stroke, stroke_width)
        )

    def blurglass_roundrect(
        self,
        pos: Position,
        size: Size,
        fill: Color,
        radius: int,
        blur: float = 4,
        shadow_width: int = 6,
        shadow_alpha: float = 0.3,
        corners: tuple[bool, bool, bool, bool] = (True, True, True, True),
        exclude_on_hash: bool = False,
    ) -> Self:
        return self.add_operation(
            "_impl_blurglass_roundrect",
            exclude_on_hash,
            (pos, size, fill, radius, blur, shadow_width, shadow_alpha, corners),
        )

    def draw_random_triangle_bg(
        self, time_color: bool, main_hue: float, size_fixed_rate: float, exclude_on_hash: bool = False
    ) -> Self:
        return self.add_operation(
            "_impl_draw_random_triangle_bg", exclude_on_hash, (time_color, main_hue, size_fixed_rate)
        )

    def _impl_text(
        self,
        text: str,
        pos: Position,
        font: FontDesc | Font,
        fill: Color | LinearGradient | AdaptiveTextColor = BLACK,
        align: str = "left",
    ):
        def adjust_overlay_alpha_by_color(overlay: Image.Image, color: Color):
            if len(color) < 4 or color[3] == 255:
                return
            overlay_alpha = overlay.getchannel("A")
            overlay_alpha = Image.eval(overlay_alpha, lambda a: int(a * color[3] / 255))
            overlay.putalpha(overlay_alpha)

        if isinstance(font, FontDesc):
            font = get_font(font.path, font.size)

        if isinstance(fill, LinearGradient):
            gradient = fill
            adaptive = None
            fill = BLACK
        elif isinstance(fill, AdaptiveTextColor):
            gradient = None
            adaptive = fill
            fill = fill.light[:3]
        else:
            gradient = None
            adaptive = None

        if (len(fill) == 3 or fill[3] == 255) and not gradient and not adaptive:
            # 不透明，非渐变，非高对比度颜色
            self._text(text, pos, font, fill, align)
        else:
            text_size = get_text_size(font, text)
            overlay_size = (text_size[0] + 10, text_size[1] + 10)
            overlay = Image.new("RGBA", overlay_size, (0, 0, 0, 0))
            p = Painter(overlay)
            p._text(text, (0, 0), font, fill=fill, align=align)

            if gradient:
                # 渐变颜色
                gradient_img = gradient.get_img(overlay_size, overlay)
                overlay = gradient_img

            elif adaptive:
                # 自适应颜色
                dark_overlay = Image.new("RGBA", overlay_size, (0, 0, 0, 0))
                dark_p = Painter(dark_overlay)
                dark_p._text(text, (0, 0), font, fill=adaptive.dark[:3], align=align)

                adjust_overlay_alpha_by_color(overlay, adaptive.light)
                adjust_overlay_alpha_by_color(dark_overlay, adaptive.dark)

                bg_img = self.img.crop(
                    (
                        pos[0] + self.offset[0],
                        pos[1] + self.offset[1],
                        pos[0] + self.offset[0] + overlay_size[0],
                        pos[1] + self.offset[1] + overlay_size[1],
                    )
                )

                if adaptive.pixelwise:
                    gray = bg_img.filter(ImageFilter.BoxBlur(radius=8)).convert("L")
                else:
                    avg_color = np.array(bg_img).reshape(-1, 4).mean(axis=0)
                    gray = Image.new("RGB", bg_img.size, tuple(avg_color[:3].astype(int))).convert("L")

                threshold = int(adaptive.threshold * 255)
                mask = gray.point(lambda p: 255 if p > threshold else 0, "L")
                overlay.paste(dark_overlay, (0, 0), mask)

            elif fill[3] < 255:
                # 半透明颜色
                adjust_overlay_alpha_by_color(overlay, fill)

            self.img.alpha_composite(overlay, (pos[0] + self.offset[0], pos[1] + self.offset[1]))

        return self

    def _impl_paste(
        self,
        sub_img: ImageSource,
        pos: Position,
        size: Size = None,
        use_shadow: bool = False,
        shadow_width: int = 6,
        shadow_alpha: float = 0.6,
        src_rect: tuple[float, float, float, float] | None = None,
    ) -> Self:
        sub_img = self._resolve_sub_img(sub_img, size, src_rect)

        if use_shadow:
            w, h = sub_img.size
            sw = shadow_width
            lw, lh = w + sw * 2, h + sw * 2
            # 获取和图像相同形状的阴影mask
            if sub_img.mode == "RGBA":
                shadow_source_mask = sub_img.getchannel("A")
            else:
                shadow_source_mask = Image.new("L", sub_img.size, 255)
            shadow_mask = Image.new("L", (lw, lh), 0)
            shadow_mask.paste(Image.new("L", sub_img.size, int(255 * shadow_alpha)), (sw, sw), shadow_source_mask)
            # 模糊获取阴影
            blurred_shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(radius=sw // 2))
            # 删除内部阴影
            inner_mask = ImageChops.invert(shadow_mask)
            blurred_shadow_mask = ImageChops.multiply(blurred_shadow_mask, inner_mask)
            # 贴入原图
            shadow = Image.new("RGBA", (lw, lh), (0, 0, 0, 255))
            shadow.putalpha(blurred_shadow_mask)
            self.img.alpha_composite(shadow, (pos[0] + self.offset[0] - sw, pos[1] + self.offset[1] - sw))

        if sub_img.mode == "RGBA":
            self.img.paste(sub_img, (pos[0] + self.offset[0], pos[1] + self.offset[1]), sub_img)
        else:
            self.img.paste(sub_img, (pos[0] + self.offset[0], pos[1] + self.offset[1]))
        return self

    def _impl_image_bg(
        self,
        image: ImageSource,
        align: ALIGN_TYPE = "c",
        mode: Literal["fit", "fill", "fixed", "repeat"] = "fit",
        blur: bool = False,
        fade: float = 0.1,
    ) -> Self:
        """Pillow reference implementation for :meth:`image_bg`.

        Keep the historical order exactly: decode -> GaussianBlur(3) -> brightness -> resize
        and paste. In particular, applying the effects after ``_resolve_sub_img`` would move
        them after the resize and change the Pillow oracle.
        """
        if not isinstance(image, Image.Image):
            image = resolve_image_source_sync(image)
        if blur:
            image = image.filter(ImageFilter.GaussianBlur(radius=3))
        if fade > 0:
            image = ImageEnhance.Brightness(image).enhance(1 - fade)

        ha, va = ALIGN_MAP[align]
        if mode == "fit":
            scale = max(self.w / image.width, self.h / image.height)
            width, height = int(image.width * scale), int(image.height * scale)
            x = (self.w - width) // 2 if ha == "c" else (0 if ha == "l" else self.w - width)
            y = (self.h - height) // 2 if va == "c" else (0 if va == "t" else self.h - height)
            return self._impl_paste(image, (x, y), (width, height))
        if mode == "fill":
            return self._impl_paste(image, (0, 0), self.size)
        if mode == "fixed":
            x = (self.w - image.width) // 2 if ha == "c" else (0 if ha == "l" else self.w - image.width)
            y = (self.h - image.height) // 2 if va == "c" else (0 if va == "t" else self.h - image.height)
            return self._impl_paste(image, (x, y))
        if mode == "repeat":
            for y in range(0, self.h, image.height):
                for x in range(0, self.w, image.width):
                    self._impl_paste(image, (x, y))
            return self
        raise ValueError(f"unsupported image background mode: {mode}")

    def _resolve_sub_img(
        self,
        sub_img: ImageSource,
        size: Size | None,
        src_rect: tuple[float, float, float, float] | None,
    ) -> Image.Image:
        if src_rect is not None:
            # Crop precedes the fit, so the whole-asset resize cache does not apply here;
            # resolve full pixels (cache-decoded for refs) and crop the slice.
            if not isinstance(sub_img, Image.Image):
                sub_img = resolve_image_source_sync(sub_img)
            sub_img = sub_img.crop(tuple(int(v) for v in src_rect))
        else:
            sub_img = _resolve_paste_source(sub_img, size)
        # Normalize the mode BEFORE resizing. Pillow forces NEAREST when resizing a palette ("P")
        # or bilevel ("1") image, so resizing first would resample the palette INDICES and only
        # then expand to RGBA — blocky edges and no alpha interpolation. Converting first lets the
        # resize run in RGBA with the real filter. A no-op for RGB/RGBA, which is nearly everything.
        if sub_img.mode not in ("RGB", "RGBA"):
            sub_img = sub_img.convert("RGBA")
        if size and size != sub_img.size:
            sub_img = sub_img.resize(size)
        return sub_img

    def _impl_paste_src(
        self,
        sub_img: ImageSource,
        pos: Position,
        size: Size = None,
        src_rect: tuple[float, float, float, float] | None = None,
    ) -> Self:
        sub_img = self._resolve_sub_img(sub_img, size, src_rect)
        # No mask -> Pillow copies every channel, alpha included (Porter-Duff Src).
        self.img.paste(sub_img, (pos[0] + self.offset[0], pos[1] + self.offset[1]))
        return self

    def _push_layer(self, kind: str, pos: Position, size: Size, payload: Any) -> Self:
        # pos_in_parent is in the current buffer's coords; the temp buffer covers only
        # the layer rect (draws are rebased there via _clip_origin, out-of-rect pixels
        # are dropped by the buffer bounds — which is what clipping means).
        pos_in_parent = (int(pos[0] + self.offset[0]), int(pos[1] + self.offset[1]))
        layer_size = (max(1, int(size[0])), max(1, int(size[1])))
        self._clip_stack.append((kind, self.img, pos_in_parent, layer_size, payload))
        self._clip_origin = (self._clip_origin[0] + pos_in_parent[0], self._clip_origin[1] + pos_in_parent[1])
        self.img = Image.new("RGBA", layer_size, TRANSPARENT)
        return self

    def _pop_layer(self, kind: str) -> tuple[Image.Image, tuple[int, int], Size, Any]:
        overlay = self.img
        frame_kind, base, pos_in_parent, size, payload = self._clip_stack.pop()
        assert frame_kind == kind, f"layer stack mismatch: popped {kind} off a {frame_kind} layer"
        self._clip_origin = (self._clip_origin[0] - pos_in_parent[0], self._clip_origin[1] - pos_in_parent[1])
        self.img = base
        return overlay, pos_in_parent, size, payload

    def _composite_layer(
        self,
        overlay: Image.Image,
        pos_in_parent: tuple[int, int],
        size: Size,
        preserve_hidden_rgb: bool = False,
    ) -> None:
        dx, dy = pos_in_parent
        if dx < 0 or dy < 0:
            overlay = overlay.crop((max(0, -dx), max(0, -dy), size[0], size[1]))
            dx, dy = max(0, dx), max(0, dy)
        if not preserve_hidden_rgb:
            self.img.alpha_composite(overlay, (dx, dy))
            return
        # Pillow's paste-lerp (Painter.paste) mixes in the DESTINATION's rgb even where the
        # destination alpha is 0, so the rgb hiding under fully transparent pixels is
        # load-bearing for any overlay whose AA edge later crosses them (an honor badge's frame
        # over the corners its mask cut away). alpha_composite forces that rgb to black — which
        # is only correct when nothing reads it. Keep the layer's own rgb there instead: those
        # pixels stay invisible, and the destination now carries what it would have carried had
        # the ops drawn straight onto it. (Where the destination is empty this is exactly a Src
        # write; see paste_src.)
        box = (dx, dy, dx + overlay.width, dy + overlay.height)
        region = Image.alpha_composite(self.img.crop(box), overlay)
        hidden = region.getchannel("A").point(lambda a: 255 if a == 0 else 0)
        region.paste(overlay, (0, 0), hidden)
        self.img.paste(region, box)

    def _impl_push_clip_roundrect(
        self,
        pos: Position,
        size: Size,
        radius: float,
        corners: tuple[bool, bool, bool, bool] = (True, True, True, True),
    ) -> Self:
        return self._push_layer("clip", pos, size, (radius, tuple(corners)))

    def _impl_pop_clip(self) -> Self:
        overlay, pos_in_parent, size, (radius, corners) = self._pop_layer("clip")
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, size[0], size[1]),
            radius=radius,
            fill=255,
            corners=corners,
        )
        overlay.putalpha(ImageChops.multiply(overlay.getchannel("A"), mask))
        self._composite_layer(overlay, pos_in_parent, size)
        return self

    def _impl_push_mask(self, mask: ImageSource, pos: Position, size: Size) -> Self:
        return self._push_layer("mask", pos, size, mask)

    def _impl_pop_mask(self) -> Self:
        overlay, pos_in_parent, size, mask_source = self._pop_layer("mask")
        mask_img = _resolve_paste_source(mask_source, size)
        # The mask is the image's ALPHA channel — the same channel Skia's DstIn samples, and
        # what ``mask.split()[3]`` means on the Pillow side.
        if mask_img.mode != "RGBA":
            mask_img = mask_img.convert("RGBA")
        if mask_img.size != size:
            mask_img = mask_img.resize(size)
        overlay.putalpha(ImageChops.multiply(overlay.getchannel("A"), mask_img.getchannel("A")))
        self._composite_layer(overlay, pos_in_parent, size, preserve_hidden_rgb=True)
        return self

    def _impl_shadow_roundrect(
        self,
        pos: Position,
        size: Size,
        radius: float,
        shadow_width: int = 6,
        shadow_alpha: float = 0.3,
    ) -> Self:
        apos = (int(pos[0] + self.offset[0]), int(pos[1] + self.offset[1]))
        w, h = int(size[0]), int(size[1])
        sw = shadow_width
        lw, lh = w + sw * 2, h + sw * 2
        shadow_mask = Image.new("L", (lw, lh), 0)
        ImageDraw.Draw(shadow_mask).rounded_rectangle(
            (sw, sw, sw + w, sw + h), radius=radius, fill=int(255 * shadow_alpha)
        )
        blurred = shadow_mask.filter(ImageFilter.GaussianBlur(radius=sw // 2))
        shadow = Image.new("RGBA", (lw, lh), (0, 0, 0, 255))
        shadow.putalpha(blurred)
        dx, dy = apos[0] - sw, apos[1] - sw
        if dx < 0 or dy < 0:
            shadow = shadow.crop((max(0, -dx), max(0, -dy), lw, lh))
            dx, dy = max(0, dx), max(0, dy)
        self.img.alpha_composite(shadow, (dx, dy))
        return self

    def _impl_paste_with_alpha_blend(
        self,
        sub_img: ImageSource,
        pos: Position,
        size: Size = None,
        alpha: float | None = None,
        use_shadow: bool = False,
        shadow_width: int = 6,
        shadow_alpha: float = 0.6,
        src_rect: tuple[float, float, float, float] | None = None,
    ) -> Self:
        sub_img = self._resolve_sub_img(sub_img, size, src_rect)
        pos = (pos[0] + self.offset[0], pos[1] + self.offset[1])
        overlay = Image.new("RGBA", sub_img.size, (0, 0, 0, 0))
        overlay.paste(sub_img, (0, 0))
        if alpha is not None:
            overlay_alpha = overlay.split()[3]
            overlay_alpha = Image.eval(overlay_alpha, lambda a: int(a * alpha))
            overlay.putalpha(overlay_alpha)

        if use_shadow:
            w, h = overlay.size
            sw = shadow_width
            lw, lh = w + sw * 2, h + sw * 2
            # 获取和图像相同形状的阴影mask
            shadow_mask = Image.new("L", (lw, lh), 0)
            shadow_mask.paste(Image.new("L", overlay.size, int(255 * shadow_alpha)), (sw, sw), overlay)
            # 模糊获取阴影
            blurred_shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(radius=sw // 2))
            # 删除内部阴影
            inner_mask = ImageChops.invert(shadow_mask)
            blurred_shadow_mask = ImageChops.multiply(blurred_shadow_mask, inner_mask)
            # 贴入原图
            shadow = Image.new("RGBA", (lw, lh), (0, 0, 0, 255))
            shadow.putalpha(blurred_shadow_mask)
            self.img.alpha_composite(shadow, (pos[0] - sw, pos[1] - sw))

        self.img.alpha_composite(overlay, pos)
        return self

    def _impl_rect(
        self,
        pos: Position,
        size: Size,
        fill: Color | Gradient,
        stroke: Color = None,
        stroke_width: int = 1,
    ) -> Self:
        if isinstance(fill, Gradient):
            gradient = fill
            fill = BLACK
        else:
            gradient = None

        pos = (pos[0] + self.offset[0], pos[1] + self.offset[1])
        bbox = (*pos, pos[0] + size[0], pos[1] + size[1])

        if fill[3] == 255 and not gradient:
            draw = ImageDraw.Draw(self.img)
            draw.rectangle(bbox, fill=fill, outline=stroke, width=stroke_width)
        else:
            overlay_size = (size[0] + 1, size[1] + 1)
            overlay = Image.new("RGBA", overlay_size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            draw.rectangle((0, 0, size[0], size[1]), fill=fill, outline=stroke, width=stroke_width)
            if gradient:
                gradient_img = gradient.get_img(overlay_size, overlay)
                overlay = gradient_img
            self.img.alpha_composite(overlay, (pos[0], pos[1]))

        return self

    def _impl_roundrect(
        self,
        pos: Position,
        size: Size,
        fill: Color | Gradient,
        radius: int,
        stroke: Color = None,
        stroke_width: int = 1,
        corners: tuple[bool, bool, bool, bool] = (True, True, True, True),
    ) -> Self:
        if isinstance(fill, Gradient):
            gradient = fill
            fill = BLACK
        else:
            gradient = None

        pos = (pos[0] + self.offset[0], pos[1] + self.offset[1])

        aa_scale = max(radius, ROUNDRECT_ANTIALIASING_TARGET_RADIUS) / radius if radius > 0 else 1.0
        aa_size = (int(size[0] * aa_scale), int(size[1] * aa_scale))
        aa_radius = radius * aa_size[0] / size[0] if size[0] > 0 else radius

        overlay_size = (aa_size[0] + 1, aa_size[1] + 1)
        overlay = Image.new("RGBA", overlay_size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw.rounded_rectangle(
            (0, 0, aa_size[0], aa_size[1]),
            fill=fill,
            radius=aa_radius,
            outline=stroke,
            width=stroke_width,
            corners=corners,
        )
        if gradient:
            gradient_img = gradient.get_img(overlay_size, overlay)
            overlay = gradient_img

        overlay = overlay.resize((size[0] + 1, size[1] + 1), Image.Resampling.BICUBIC)
        self.img.alpha_composite(overlay, (pos[0], pos[1]))

        return self

    def _impl_pieslice(
        self,
        pos: Position,
        size: Size,
        start_angle: float,
        end_angle: float,
        fill: Color,
        stroke: Color = None,
        stroke_width: int = 1,
    ) -> Self:
        if isinstance(fill, Gradient):
            gradient = fill
            fill = BLACK
        else:
            gradient = None

        pos = (pos[0] + self.offset[0], pos[1] + self.offset[1])
        bbox = (*pos, pos[0] + size[0], pos[1] + size[1])

        if fill[3] == 255 and not gradient:
            draw = ImageDraw.Draw(self.img)
            draw.pieslice(bbox, start_angle, end_angle, fill=fill, width=stroke_width, outline=stroke)
        else:
            overlay_size = (size[0] + 1, size[1] + 1)
            overlay = Image.new("RGBA", overlay_size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            draw.pieslice(
                (0, 0, size[0], size[1]), start_angle, end_angle, fill=fill, width=stroke_width, outline=stroke
            )
            if gradient:
                gradient_img = gradient.get_img(overlay_size, overlay)
                overlay = gradient_img
            self.img.alpha_composite(overlay, (pos[0], pos[1]))

        return self

    def _impl_blurglass_roundrect(
        self,
        pos: Position,
        size: Size,
        fill: Color,
        radius: int,
        blur: float = 4,
        shaodow_width: int = 6,
        shadow_alpha: float = 0.3,
        corners: tuple[bool, bool, bool, bool] = (True, True, True, True),
        edge_strength: float = 0.6,
    ) -> Self:
        if min(size) <= 0:
            return self

        sw = shaodow_width
        pos = (pos[0] + self.offset[0], pos[1] + self.offset[1])
        draw_pos = (pos[0] - sw, pos[1] - sw)
        draw_size = (size[0] + sw * 2, size[1] + sw * 2)

        aa_scale = max(radius, ROUNDRECT_ANTIALIASING_TARGET_RADIUS) / radius if radius > 0 else 1.0
        aa_size = (int(draw_size[0] * aa_scale), int(draw_size[1] * aa_scale))
        aa_sw = int(sw * aa_scale)
        aa_r = radius * aa_size[0] / draw_size[0] if draw_size[0] > 0 else radius
        aa_resize_method = Image.Resampling.BILINEAR if aa_scale < 2 else Image.Resampling.BICUBIC

        alpha = fill[3] if isinstance(fill, tuple) and len(fill) == 4 else 0
        bg_offset = int(24 * min(blur / 6, alpha / 200))
        bg_offset = min(bg_offset, draw_size[0] - bg_offset, draw_size[1] - bg_offset)
        bg_region = (
            pos[0] + bg_offset // 2,
            pos[1] + bg_offset // 2,
            pos[0] + size[0] - bg_offset // 2,
            pos[1] + size[1] - bg_offset // 2,
        )

        if isinstance(fill, Gradient):
            # 填充渐变色
            bg = fill.get_img((bg_region[2] - bg_region[0], bg_region[3] - bg_region[1]))
        elif len(fill) == 3 or fill[3] == 255:
            # 填充纯色
            if len(fill) == 3:
                fill = (*fill, 255)
            bg = Image.new("RGBA", (bg_region[2] - bg_region[0], bg_region[3] - bg_region[1]), fill)
        else:
            # 复制pos位置的size大小的原图模糊并混合颜色
            bg = self.img.crop(bg_region)
            if blur > 0:
                downsample = max(1, int(blur // 2))
                if downsample > 1:
                    bg = bg.resize(
                        (max(1, bg.width // downsample), max(1, bg.height // downsample)),
                        Image.Resampling.BILINEAR,
                    )
                blur_method = ImageFilter.GaussianBlur if downsample >= 2 else ImageFilter.BoxBlur
                bg = bg.filter(blur_method(radius=blur / downsample))
            bg.alpha_composite(Image.new("RGBA", bg.size, fill))

        # 超分绘制圆角矩形，缩放到目标大小
        overlay = Image.new("RGBA", (aa_size[0], aa_size[1]), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw.rounded_rectangle(
            (aa_sw, aa_sw, aa_size[0] - aa_sw - 1, aa_size[1] - aa_sw - 1), fill=BLACK, radius=aa_r, corners=corners
        )
        overlay = overlay.resize((draw_size[0], draw_size[1]), aa_resize_method)

        # 取得mask
        inner_mask = overlay.copy()
        bg_mask = overlay.crop((sw, sw, sw + size[0], sw + size[1]))

        # 通过模糊底图获取阴影，然后删除内部阴影
        adjust_image_alpha_inplace(overlay, shadow_alpha, method="multiply")
        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=int(sw * 0.5)))
        overlay = ImageChops.multiply(overlay, ImageChops.invert(inner_mask))

        # 用圆角矩形mask裁剪并粘贴背景
        bg = bg.resize(size, Image.Resampling.BILINEAR)
        bg.putalpha(bg_mask.split()[3])
        overlay.alpha_composite(bg, (sw, sw))

        # 边缘效果
        if edge_strength is not None and edge_strength > 0:
            edge_width = min(4, min(draw_size) // 16, radius // 2)
            if edge_width > 0:
                edge_overlay = Image.new("RGBA", (aa_size[0], aa_size[1]), TRANSPARENT)
                draw = ImageDraw.Draw(edge_overlay)
                ew, aa_ew = edge_width, int(edge_width * aa_scale)
                draw.rounded_rectangle(
                    (aa_sw, aa_sw, aa_size[0] - aa_sw - 1, aa_size[1] - aa_sw - 1),
                    outline=WHITE,
                    width=aa_ew,
                    radius=aa_r,
                    corners=corners,
                )

                edge_overlay = edge_overlay.resize(draw_size, aa_resize_method)
                alpha1, alpha2 = int(255 * edge_strength), int(255 * edge_strength * 0.75)
                lt_points, rb_points = ((0, 0), (0.8, 0.4)), ((0.6, 0.8), (1.0, 1.0))
                lt_colors = ((255, 255, 255, alpha1), (255, 255, 255, 0))
                rb_colors = ((255, 255, 255, 0), (255, 255, 255, alpha2))
                w, h = draw_size[0], draw_size[1]

                def get_grad_p(
                    p1: tuple[int, int], p2: tuple[int, int], _pos: tuple[int, int], _size: tuple[int, int]
                ) -> dict[str, tuple[float, float]]:
                    p1, p2 = (p1[0] * w, p1[1] * h), (p2[0] * w, p2[1] * h)
                    newp1 = ((p1[0] - _pos[0]) / _size[0], (p1[1] - _pos[1]) / _size[1])
                    newp2 = ((p2[0] - _pos[0]) / _size[0], (p2[1] - _pos[1]) / _size[1])
                    return {"p1": newp1, "p2": newp2}

                edge_color_overlay = Image.new("RGBA", draw_size, TRANSPARENT)
                t_pos, t_size = (sw, sw), (w - sw * 2, ew)
                edge_color_t = LinearGradient(*lt_colors, **get_grad_p(*lt_points, t_pos, t_size)).get_img(t_size)
                edge_color_overlay.paste(edge_color_t, t_pos)
                l_pos, l_size = (sw, sw), (ew, h - sw * 2)
                edge_color_l = LinearGradient(*lt_colors, **get_grad_p(*lt_points, l_pos, l_size)).get_img(l_size)
                edge_color_overlay.paste(edge_color_l, l_pos)
                lt_pos, lt_size = (sw, sw), (radius, radius)
                edge_color_lt = LinearGradient(*lt_colors, **get_grad_p(*lt_points, lt_pos, lt_size)).get_img(lt_size)
                edge_color_overlay.paste(edge_color_lt, lt_pos)

                r_pos, r_size = (w - ew - sw, sw), (ew, h - sw * 2)
                edge_color_r = LinearGradient(*rb_colors, **get_grad_p(*rb_points, r_pos, r_size)).get_img(r_size)
                edge_color_overlay.paste(edge_color_r, r_pos)
                b_pos, b_size = (sw, h - ew - sw), (w - sw * 2, ew)
                edge_color_b = LinearGradient(*rb_colors, **get_grad_p(*rb_points, b_pos, b_size)).get_img(b_size)
                edge_color_overlay.paste(edge_color_b, b_pos)
                rb_pos, rb_size = (w - radius - sw, h - radius - sw), (radius, radius)
                edge_color_rb = LinearGradient(*rb_colors, **get_grad_p(*rb_points, rb_pos, rb_size)).get_img(rb_size)
                edge_color_overlay.paste(edge_color_rb, rb_pos)

                edge_overlay = ImageChops.multiply(edge_overlay, edge_color_overlay)
                overlay.alpha_composite(edge_overlay)

        # 贴回原图
        self.img.alpha_composite(overlay, (draw_pos[0], draw_pos[1]))
        return self

    def _impl_draw_random_triangle_bg(self, use_time_color: bool, main_hue: float, size_fixed_rate: float):
        """Draw the shared triangle-background spec. The scatter is NOT rolled here — see
        ``base/triangle_bg.py``: both backends draw the same generated list, so neither the two
        backends nor two runs of the same backend can disagree about it any more."""
        w, h = self.size
        spec = build_triangle_bg(w, h, background_hour(), use_time_color, main_hue, size_fixed_rate)
        primary_p1, primary_p2, overlay_p1, overlay_p2 = gradient_points(w, h)

        s = 4  # the gradients are smooth; build them at quarter size and LANCZOS back up
        bg = LinearGradient(c1=spec.grad1, c2=spec.grad2, p1=primary_p1, p2=primary_p2).get_img((w // s, h // s))
        bg.alpha_composite(
            LinearGradient(c1=spec.overlay1, c2=spec.overlay2, p1=overlay_p1, p2=overlay_p2).get_img((w // s, h // s))
        )
        bg.alpha_composite(Image.new("RGBA", (w // s, h // s), (255, 255, 255, spec.white_alpha)))
        bg = bg.resize((w, h), Image.Resampling.LANCZOS)

        for tri in spec.triangles:
            # Skia strokes the path at float coordinates, so the overlay's ORIGIN is snapped to the
            # pixel grid and the vertices keep their subpixel offset inside it. Rounding the centre
            # instead (the old `int(x) - width // 2`) shifted every triangle by up to half a pixel
            # relative to Skia, which no amount of seed-sharing would have fixed.
            span = tri.size * 2
            overlay = Image.new("RGBA", (span, span), (0, 0, 0, 0))
            ox, oy = math.floor(tri.x - tri.size), math.floor(tri.y - tri.size)
            cx, cy = tri.x - ox, tri.y - oy
            radius = tri.size * 0.56
            type_angle_offset = (0, 18, -18)[tri.type % 3]
            points = []
            for idx in range(3):
                angle = math.radians(tri.rot + type_angle_offset + idx * 120 - 90)
                points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
            ImageDraw.Draw(overlay).polygon(points, fill=tri.color)
            bg.alpha_composite(overlay, (ox, oy))

        self.img.paste(bg, self.offset)
