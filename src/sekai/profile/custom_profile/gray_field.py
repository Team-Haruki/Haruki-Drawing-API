"""Immutable gray8 glyph fields, independent of any image renderer.

These are sampled TMP data, not encoded images or page rasters. Native resizing
uses the same separable uint8 filter as the legacy L-mode bicubic operation.
"""

from dataclasses import dataclass
import importlib
import math
import operator

MAX_FIELD_PIXELS = 16 * 1024 * 1024
MAX_FIELD_DIMENSION = 32767


def field_size(size: tuple[int, int]) -> tuple[int, int]:
    width, height = (operator.index(value) for value in size)
    if min(width, height) <= 0 or max(width, height) > MAX_FIELD_DIMENSION or width * height > MAX_FIELD_PIXELS:
        raise ValueError(f"gray8 dimensions exceed raster limits: {width}x{height}")
    return width, height


@dataclass(frozen=True, slots=True)
class GrayField:
    width: int
    height: int
    pixels: bytes

    def __post_init__(self):
        field_size(self.size)
        if not isinstance(self.pixels, bytes):
            raise TypeError("gray8 pixels must be immutable bytes")
        if len(self.pixels) != self.width * self.height:
            raise ValueError("gray8 source length does not match dimensions")

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def __array__(self, dtype=None, copy=None):
        import numpy as np

        array = np.frombuffer(self.pixels, dtype=np.uint8).reshape(self.height, self.width)
        if dtype is not None and np.dtype(dtype) != array.dtype:
            if copy is False:
                raise ValueError("dtype conversion requires copying gray8 pixels")
            return array.astype(dtype)
        return array.copy() if copy else array

    def crop(self, box: tuple[int, int, int, int]) -> "GrayField":
        """Integer field window, with zero padding outside the source bounds."""
        left, top, right, bottom = (operator.index(value) for value in box)
        width, height = field_size((right - left, bottom - top))
        if (left, top, right, bottom) == (0, 0, self.width, self.height):
            return self
        output = bytearray(width * height)
        x0, x1 = max(0, left), min(self.width, right)
        if x0 < x1:
            source = memoryview(self.pixels)
            for y in range(max(0, top), min(self.height, bottom)):
                offset = (y - top) * width + x0 - left
                output[offset : offset + x1 - x0] = source[y * self.width + x0 : y * self.width + x1]
        return GrayField(width, height, bytes(output))

    def resize_bicubic(self, size: tuple[int, int]) -> "GrayField":
        width, height = field_size(size)
        if (width, height) == self.size:
            return self
        try:
            native = importlib.import_module("haruki_skia_renderer")
            if getattr(native, "GRAY_FIELD_CAPABILITY", 0) < 1:
                raise ImportError("native gray8 API is unavailable")
            pixels = native.resize_gray8_bicubic(self.pixels, self.size, (width, height))
        except (ImportError, AttributeError, RuntimeError):
            # Keep the existing fail-open contract until the full retirement gate passes.
            # This is an explicit adapter; import guards also catch swallowed attempts.
            from .pillow_fields import resize_gray_bicubic

            return resize_gray_bicubic(self, (width, height))
        return GrayField(width, height, pixels)

    def transform_bicubic(self, size: tuple[int, int], inverse: tuple[float, ...]) -> "GrayField":
        width, height = field_size(size)
        matrix = tuple(float(value) for value in inverse)
        if len(matrix) != 6 or not all(math.isfinite(value) for value in matrix):
            raise ValueError("gray8 affine needs six finite coefficients")
        try:
            native = importlib.import_module("haruki_skia_renderer")
            if getattr(native, "GRAY_FIELD_CAPABILITY", 0) < 2:
                raise ImportError("native gray8 affine API is unavailable")
            pixels = native.transform_gray8_bicubic(self.pixels, self.size, (width, height), matrix)
        except (ImportError, AttributeError, RuntimeError):
            from .pillow_fields import transform_gray_bicubic

            return transform_gray_bicubic(self, (width, height), matrix)
        return GrayField(width, height, pixels)
