import os
import math
import glob
import emoji
import random
import hashlib
import asyncio
import colorsys
import numpy as np

from pilmoji import Pilmoji
from typing import Union, Any, Self
from datetime import datetime, timedelta
from PIL.ImageFont import ImageFont as Font
from pilmoji import getsize as getsize_emoji
from pilmoji.source import GoogleEmojiSource
from dataclasses import dataclass, is_dataclass, fields
from PIL import Image, ImageFont, ImageDraw, ImageFilter, ImageChops

from .img_utils import mix_image_by_color, adjust_image_alpha_inplace

DEBUG = True


def debug_print(*args, **kwargs) -> None:
    if DEBUG:
        print(*args, **kwargs, flush=True)


def get_memo_usage() -> Union[float, int]:
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

    def update(s: Union[str, bytes]) -> None:
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

        # 容器类型
        elif isinstance(_obj, (list, tuple)):
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
        elif hasattr(_obj, "__iter__") and not isinstance(_obj, (str, bytes)):
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
            except:
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

Color = Union[tuple[int, int, int, int], tuple[int, int, int], list[int]]
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

FONT_DIR = "/Users/deseer/PycharmProjects/Haruki-Drawing-API/data"
DEFAULT_FONT = "SourceHanSansSC-Regular"
DEFAULT_BOLD_FONT = "SourceHanSansSC-Bold"
DEFAULT_HEAVY_FONT = "SourceHanSansSC-Heavy"
DEFAULT_EMOJI_FONT = "EmojiOneColor-SVGinOT"


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


# =========================== 工具函数 =========================== #


@dataclass
class FontDesc:
    path: str
    size: int


@dataclass
class FontCacheEntry:
    font: Font
    last_used: datetime


FONT_CACHE_MAX_NUM = 128
font_cache: dict[str, FontCacheEntry] = {}


def crop_by_align(original_size: int, crop_size: int, align: int) -> tuple[int, int, int, int]:
    w, h = original_size
    cw, ch = crop_size
    assert cw <= w and ch <= h, "Crop size must be smaller than original size"
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


def lerp_color(
    c1: Union[list[int], tuple[int, ...]], c2: Union[list[int], tuple[int, ...]], t: float
) -> tuple[int, ...]:
    ret = []
    for i in range(len(c1)):
        ret.append(max(0, min(255, int(c1[i] * (1 - t) + c2[i] * t))))
    return tuple(ret)


def adjust_color(
    c: Union[list[int], tuple[int, ...]], r: int = None, g: int = None, b: int = None, a: int = None
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
    global font_cache
    key = f"{path}_{size}"
    paths = [
        path,
        os.path.join(FONT_DIR, path),
        os.path.join(FONT_DIR, path + ".ttf"),
        os.path.join(FONT_DIR, path + ".otf"),
    ]
    if key not in font_cache:
        font: Union[ImageFont.ImageFont, ImageFont.FreeTypeFont] | None = None
        for path in paths:
            if os.path.exists(path):
                font = ImageFont.truetype(path, size)
                break
        if font is None:
            raise FileNotFoundError(f"Font file not found: {path}")
        font_cache[key] = FontCacheEntry(font, datetime.now())
        # 清理过期的字体缓存
        while len(font_cache) > FONT_CACHE_MAX_NUM:
            oldest_key = min(font_cache, key=lambda k: font_cache[k].last_used)
            del font_cache[oldest_key]
    return font_cache[key].font


def get_text_size(font: Font, text: str) -> Size:
    if emoji.emoji_count(text) > 0:
        return getsize_emoji(text, font=font)
    else:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]


def get_text_offset(font: Font, text: str) -> Position:
    bbox = font.getbbox(text)
    return bbox[0], bbox[1]


def resize_keep_ratio(
    img: Image.Image, max_size: Union[int, float], mode: str = "long", scale: int = None
) -> Image.Image:
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
        img = Image.fromarray(self.get_colors(size), "RGBA")
        if mask:
            assert mask.size == size, "Mask size must match image size"
            if mask.mode == "RGBA":
                mask = mask.split()[3]
            else:
                mask = mask.convert("L")
            img.putalpha(mask)
        return img


class LinearGradient(Gradient):
    def __init__(self, c1: Color, c2: Color, p1: Position, p2: Position, method: str = "separate") -> None:
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
            t = np.average(vector_pixel_to_p1 / vector_p2_to_p1, axis=-1)
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


# =========================== 绘图类 =========================== #


@dataclass
class PainterOperation:
    offset: Position
    size: Size
    func: Union[str, callable]
    args: list
    exclude_on_hash: bool

    def image_to_id(self, img_dict: dict[int, Image.Image]) -> None:
        if isinstance(self.args, tuple):
            self.args = list(self.args)
        for i in range(len(self.args)):
            if isinstance(self.args[i], Image.Image):
                img_id = id(self.args[i])
                img_dict[img_id] = self.args[i]
                self.args[i] = f"%%image%%{img_id}"

    def id_to_image(self, img_dict: dict[Union[int, str], Image.Image]) -> None:
        if isinstance(self.args, tuple):
            self.args = list(self.args)
        for i in range(len(self.args)):
            if isinstance(self.args[i], str) and self.args[i].startswith("%%image%%"):
                img_id = int(self.args[i][9:])
                self.args[i] = img_dict[img_id]


class Painter:
    def __init__(self, img: Image.Image = None, size: tuple[int, int] = None) -> None:
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

    def _text(self, text: str, pos: Position, font: Font, fill: Color = BLACK, align: str = "left") -> Self:
        std_size = get_text_size(font, "哇")
        has_emoji = emoji.emoji_count(text) > 0
        if not has_emoji:
            draw = ImageDraw.Draw(self.img)
            text_offset = (0, -std_size[1])
            pos = (pos[0] - text_offset[0] + self.offset[0], pos[1] - text_offset[1] + self.offset[1])
            draw.text(pos, text, font=font, fill=fill, align=align, anchor="ls")
        else:
            with Pilmoji(self.img, source=GoogleEmojiSource) as pilmoji:
                text_offset = (0, -std_size[1])
                pos = (pos[0] - text_offset[0] + self.offset[0], pos[1] - text_offset[1] + self.offset[1])
                pilmoji.text(
                    pos, text, font=font, fill=fill, align=align, emoji_position_offset=(0, -std_size[1]), anchor="ls"
                )
        return self

    @staticmethod
    def _execute(
        operations: list[PainterOperation], img: Image.Image, size: tuple[int, int], image_dict: dict[str, Image.Image]
    ) -> Image.Image:
        t = datetime.now()
        debug_print(f"Sub process enter memory usage: {get_memo_usage()} MB")
        if img is None:
            img = Image.new("RGBA", size, TRANSPARENT)
        p = Painter(img, size)
        for op in operations:
            op.id_to_image(image_dict)
            # debug_print(f"Executing: {op}")
            p.offset = op.offset
            p.size = op.size
            p.w, p.h = op.size
            func = getattr(p, op.func) if isinstance(op.func, str) else op.func
            # kwargs = {}
            # for key, value in get_type_hints(func).items():
            #     if value == Painter:
            #         kwargs[key] = p
            # func(*op.args, **kwargs)
            func(*op.args)
            # debug_print(f"Method {op.name} executed, current memory usage: {get_memo_usage()} MB")
        debug_print(f"Sub process use time: {datetime.now() - t}")
        return p.img

    async def get(self, cache_key: str = None) -> Image.Image:
        # 使用缓存
        if cache_key is not None:
            t = datetime.now()
            debug_print(f"Cache key: {cache_key}")
            op_hash = await asyncio.to_thread(deterministic_hash, {"key": cache_key, "op": self.operations})
            debug_print(f"Cache key: {cache_key}, op_hash: {op_hash}, elapsed: {datetime.now() - t}")

            paths = glob.glob(os.path.join(PAINTER_CACHE_DIR, f"{cache_key}__*.png"))
            if paths:
                path = paths[0]
                if path.endswith(f"{cache_key}__{op_hash}.png"):
                    # 如果hash相同则直接返回缓存的图片
                    debug_print(f"Using cached image: {path}")
                    img = Image.open(path)
                    img.load()
                    return img
                else:
                    # 否则清空缓存并重新绘图
                    for p in paths:
                        try:
                            os.remove(p)
                        except Exception as e:
                            print(f"Failed to remove cache file {p}: {e}")
                    debug_print(f"Cache mismatch, removed {len(paths)} files")

        debug_print(f"Main process memory usage: {get_memo_usage()} MB")

        # 收集所有图片对象到字典中
        image_dict = {}
        for op in self.operations:
            op.image_to_id(image_dict)
        total_img_size = 0
        for img in image_dict.values():
            total_img_size += img.size[0] * img.size[1] * 4
        debug_print(f"image_dict len: {len(image_dict)}, total size: {total_img_size // 1024 // 1024} MB")

        for op in self.operations:
            debug_print(str(op))

        # 执行绘图操作
        t = datetime.now()

        # global _painter_pool
        # self.img = await _painter_pool.submit(Painter._execute, self.operations, self.img, self.size, image_dict)
        self.img = await asyncio.to_thread(Painter._execute, self.operations, self.img, self.size, image_dict)

        self.operations = []
        debug_print(f"Painter executed in {datetime.now() - t}")

        # 保存缓存
        if cache_key is not None:
            try:
                cache_path = os.path.join(PAINTER_CACHE_DIR, f"{cache_key}__{op_hash}.png")
                os.makedirs(PAINTER_CACHE_DIR, exist_ok=True)
                self.img.save(cache_path, format="PNG")
            except:
                debug_print(f"Failed to save cache for {cache_key}")

        return self.img

    def add_operation(self, func: Union[str, callable], exclude_on_hash: bool, args: Any):
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
        paths = glob.glob(os.path.join(PAINTER_CACHE_DIR, f"{cache_key}__*.png"))
        ok = 0
        for p in paths:
            try:
                os.remove(p)
                ok += 1
            except Exception as e:
                print(f"Failed to remove cache file {p}: {e}")
        return ok

    @staticmethod
    def get_cache_key_mtimes() -> dict[str, datetime]:
        paths = glob.glob(os.path.join(PAINTER_CACHE_DIR, f"*.png"))
        cache_keys = {}
        for p in paths:
            mtime = os.path.getmtime(p)
            cache_key = os.path.basename(p).split("__")[0]
            cache_keys[cache_key] = datetime.fromtimestamp(mtime)
        return cache_keys

    def set_region(self, pos: Position, size: Size) -> Self:
        assert isinstance(pos[0], int) and isinstance(pos[1], int), "Position must be integer"
        assert isinstance(size[0], int) and isinstance(size[1], int), "Size must be integer"
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
        font: Union[FontDesc, Font],
        fill: Union[Color, LinearGradient] = BLACK,
        align: str = "left",
        exclude_on_hash: bool = False,
    ) -> Self:
        return self.add_operation("_impl_text", exclude_on_hash, (text, pos, font, fill, align))

    def paste(
        self,
        sub_img: Image.Image,
        pos: Position,
        size: Size = None,
        exclude_on_hash: bool = False,
    ) -> Self:
        return self.add_operation("_impl_paste", exclude_on_hash, (sub_img, pos, size))

    def paste_with_alphablend(
        self,
        sub_img: Image.Image,
        pos: Position,
        size: Size = None,
        alpha: float = None,
        exclude_on_hash: bool = False,
    ) -> Self:
        return self.add_operation("_impl_paste_with_alphablend", exclude_on_hash, (sub_img, pos, size, alpha))

    def rect(
        self,
        pos: Position,
        size: Size,
        fill: Union[Color, Gradient],
        stroke: Color = None,
        stroke_width: int = 1,
        exclude_on_hash: bool = False,
    ) -> Self:
        return self.add_operation("_impl_rect", exclude_on_hash, (pos, size, fill, stroke, stroke_width))

    def roundrect(
        self,
        pos: Position,
        size: Size,
        fill: Union[Color, Gradient],
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
        font: Union[FontDesc, Font],
        fill: Union[Color, LinearGradient] = BLACK,
        align: str = "left",
    ) -> Self:
        if isinstance(font, FontDesc):
            font = get_font(font.path, font.size)
        if isinstance(fill, LinearGradient):
            gradient = fill
            fill = BLACK
        else:
            gradient = None

        if (len(fill) == 3 or fill[3] == 255) and not gradient:
            self._text(text, pos, font, fill, align)
        else:
            text_size = get_text_size(font, text)
            overlay_size = (text_size[0] + 10, text_size[1] + 10)
            overlay = Image.new("RGBA", overlay_size, (0, 0, 0, 0))
            p = Painter(overlay)
            p._text(text, (0, 0), font, fill=fill, align=align)
            if gradient:
                gradient_img = gradient.get_img(overlay_size, overlay)
                overlay = gradient_img
            elif fill[3] < 255:
                overlay_alpha = overlay.split()[3]
                overlay_alpha = Image.eval(overlay_alpha, lambda a: int(a * fill[3] / 255))
                overlay.putalpha(overlay_alpha)
            self.img.alpha_composite(overlay, (pos[0] + self.offset[0], pos[1] + self.offset[1]))
        return self

    def _impl_paste(self, sub_img: Image.Image, pos: Position, size: Size = None) -> Self:
        if size and size != sub_img.size:
            sub_img = sub_img.resize(size)
        if sub_img.mode not in ("RGB", "RGBA"):
            sub_img = sub_img.convert("RGBA")
        if sub_img.mode == "RGBA":
            self.img.paste(sub_img, (pos[0] + self.offset[0], pos[1] + self.offset[1]), sub_img)
        else:
            self.img.paste(sub_img, (pos[0] + self.offset[0], pos[1] + self.offset[1]))
        return self

    def _impl_paste_with_alphablend(
        self, sub_img: Image.Image, pos: Position, size: Size = None, alpha: float = None
    ) -> Self:
        if size and size != sub_img.size:
            sub_img = sub_img.resize(size)
        pos = (pos[0] + self.offset[0], pos[1] + self.offset[1])
        overlay = Image.new("RGBA", sub_img.size, (0, 0, 0, 0))
        overlay.paste(sub_img, (0, 0))
        if alpha is not None:
            overlay_alpha = overlay.split()[3]
            overlay_alpha = Image.eval(overlay_alpha, lambda a: int(a * alpha))
            overlay.putalpha(overlay_alpha)
        self.img.alpha_composite(overlay, pos)
        return self

    def _impl_rect(
        self,
        pos: Position,
        size: Size,
        fill: Union[Color, Gradient],
        stroke: Color = None,
        stroke_width: int = 1,
    ) -> Self:
        if isinstance(fill, Gradient):
            gradient = fill
            fill = BLACK
        else:
            gradient = None

        pos = (pos[0] + self.offset[0], pos[1] + self.offset[1])
        bbox = pos + (pos[0] + size[0], pos[1] + size[1])

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
        fill: Union[Color, Gradient],
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
        bbox = pos + (pos[0] + size[0], pos[1] + size[1])

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
                bg = bg.filter(ImageFilter.GaussianBlur(radius=blur))
            bg = mix_image_by_color(bg, fill)

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

    def _impl_draw_random_triangle_bg(self, time_color: bool, main_hue: float, size_fixed_rate: float):
        timecolors = [
            (0, 0.57, 4.0, 0.1),
            (5, 0.57, 2.0, 0.2),
            (9, 0.57, 1.0, 0.8),
            (12, 0.57, 1.0, 1.0),
            (15, 0.57, 1.0, 0.8),
            (19, 0.57, 2.0, 0.2),
            (24, 0.57, 4.0, 0.1),
        ]

        def get_timecolor(t: datetime) -> tuple | None:
            if t.hour < timecolors[0][0]:
                return timecolors[0][1:]
            elif t.hour >= timecolors[-1][0]:
                return timecolors[-1][1:]
            for i in range(0, len(timecolors) - 1):
                if timecolors[i][0] <= t.hour < timecolors[i + 1][0]:
                    hour1, h1, s1, l1 = timecolors[i]
                    hour2, h2, s2, l2 = timecolors[i + 1]
                    t1 = datetime(t.year, t.month, t.day, hour1)
                    if hour2 == 24:
                        t2 = datetime(t.year, t.month, t.day, 0) + timedelta(days=1)
                    else:
                        t2 = datetime(t.year, t.month, t.day, hour2)
                    x = (t - t1) / (t2 - t1)
                    return (
                        h1 + (h2 - h1) * x,
                        s1 + (s2 - s1) * x,
                        l1 + (l2 - l1) * x,
                    )
            return None

        w, h = self.size
        if time_color:
            mh, ms, ml = get_timecolor(datetime.now())
        else:
            mh = main_hue
            ms = 1.0
            ml = 1.0

        def h2c(_h: float, _s: float, _l: float, a=255) -> list[int]:
            _h = (_h + 1.0) % 1.0
            r, g, b = colorsys.hls_to_rgb(_h, _l * ml, _s * ms)
            return [int(255 * c) for c in (r, g, b)] + [a]

        ofs, s = 0.025, 4
        bg = LinearGradient(c1=h2c(mh, 0.5, 1.0), c2=h2c(mh + ofs, 0.9, 0.5), p1=(0, 1), p2=(1, 0)).get_img(
            (w // s, h // s)
        )
        bg.alpha_composite(
            LinearGradient(c1=h2c(mh, 0.9, 0.7, 100), c2=h2c(mh - ofs, 0.5, 0.5, 100), p1=(0, 0), p2=(1, 1)).get_img(
                (w // s, h // s)
            )
        )
        bg.alpha_composite(Image.new("RGBA", (w // s, h // s), (255, 255, 255, 100)))
        bg = bg.resize((w, h), Image.Resampling.LANCZOS)

        tri1 = Image.new("RGBA", (64, 64), (255, 255, 255, 0))
        draw = ImageDraw.Draw(tri1)
        draw.polygon([(0, 0), (64, 32), (32, 64)], fill=WHITE)

        tri2 = Image.new("RGBA", (64, 64), (255, 255, 255, 0))
        draw = ImageDraw.Draw(tri2)
        draw.polygon([(0, 0), (64, 32), (32, 64)], outline=WHITE, width=4)

        def draw_tri(
            x: float, y: float, rot: float, size: int, color: float | tuple[float, ...] | str, type_: int
        ) -> None:
            img = tri1 if type_ == 0 else tri2
            img = img.resize((size, size), Image.Resampling.BILINEAR)
            img = img.rotate(rot, expand=True)
            img = ImageChops.multiply(img, Image.new("RGBA", img.size, color))
            bg.alpha_composite(img, (int(x) - img.width // 2, int(y) - img.height // 2))

        preset_colors = [
            (255, 189, 246),
            (183, 246, 255),
            (255, 247, 146),
        ]

        factor = min(w, h) / 2048 * 1.5
        size_factor = 1.0 + (factor - 1.0) * (1.0 - size_fixed_rate)
        dense_factor = 1.0 + (factor * factor - 1.0) * size_fixed_rate

        def rand_tri(num: int, sz: tuple[float, float]) -> None:
            for i in range(num):
                x = random.uniform(0, w)
                y = random.uniform(0, h)
                if x < 0 or x >= w or y < 0 or y >= h:
                    continue
                rot = random.uniform(0, 360)
                size = max(1, min(1000, int(random.normalvariate(sz[0], sz[1]))))
                dist = ((x - w // 2) / w * 2) ** 2 + ((y - h // 2) / h * 2) ** 2
                size = int(size * dist)
                size_alpha_factor, std_size = 1.0, 32 * size_factor
                if size < std_size:
                    size_alpha_factor = size / std_size
                if size > std_size:
                    size_alpha_factor = 1.0 - (size - std_size * 1.5) / (std_size * 1.5)
                alpha = int(random.normalvariate(50, 200) * max(0, min(1.2, size_alpha_factor) * (ml**0.5)))
                if alpha <= 0:
                    continue
                color = random.choice(preset_colors + [(255, 255, 255)] * 0)
                color = (*color, alpha)
                type_ = i % 3 // 2
                draw_tri(x, y, rot, size, color, type_)

        rand_tri(int(80 * dense_factor), (48 * size_factor, 16 * size_factor))
        rand_tri(int(800 * dense_factor), (16 * size_factor, 16 * size_factor))

        self.img.paste(bg, self.offset)
