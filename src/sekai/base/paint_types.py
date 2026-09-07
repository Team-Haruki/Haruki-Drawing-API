"""Shared paint values and geometry. Importing these types never loads Pillow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import numpy as np

if TYPE_CHECKING:
    from PIL import Image

Color = tuple[int, int, int, int] | tuple[int, int, int] | list[int]


Position = tuple[int, int]


Size = tuple[int, int]


ImageSampling = Literal["nearest", "linear", "catmull_rom", "pillow_bicubic", "pillow_lanczos", "pillow_bilinear"]


ImageTintMode = Literal["multiply", "recolor"]


ImagePasteBlend = Literal["paste_lerp", "src_over", "src"]


@dataclass(frozen=True)
class ImageTint:
    """Backend-neutral color decoration for an image paste.

    ``multiply`` performs component-wise RGBA multiplication. ``recolor`` treats the source
    alpha as a stencil, replaces RGB with ``color``, and lets the color alpha scale that stencil.
    Both map directly to the existing Render-IR Image tint modes.
    """

    color: Color
    mode: ImageTintMode = "multiply"

    def __post_init__(self) -> None:
        if len(self.color) not in (3, 4):
            raise ValueError("image tint color must have 3 or 4 channels")
        if self.mode not in ("multiply", "recolor"):
            raise ValueError(f"unsupported image tint mode: {self.mode}")


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


@dataclass
class FontDesc:
    path: str
    size: int


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


class Gradient:
    def get_colors(self, size: Size) -> np.ndarray:
        # [W, H, 4]
        raise NotImplementedError()

    def get_img(self, size: Size, mask: Image.Image = None) -> Image.Image:
        from .pillow_gradients import gradient_image

        return gradient_image(self, size, mask)


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


# Pillow-compatible integer filter ids are part of the legacy image-source API.
# Keeping them here avoids importing a raster backend to describe a resize.
from enum import IntEnum


class RasterResample(IntEnum):
    NEAREST = 0
    LANCZOS = 1
    BILINEAR = 2
    BICUBIC = 3
    BOX = 4
    HAMMING = 5


def image_resample_filter(sampling: ImageSampling | None) -> RasterResample:
    if sampling is None:
        return RasterResample.BICUBIC
    return {
        "nearest": RasterResample.NEAREST,
        "linear": RasterResample.BILINEAR,
        "catmull_rom": RasterResample.BICUBIC,
        "pillow_bicubic": RasterResample.BICUBIC,
        "pillow_bilinear": RasterResample.BILINEAR,
        "pillow_lanczos": RasterResample.LANCZOS,
    }[sampling]
