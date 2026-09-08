"""Unquantized distance samples; immutable little-endian float32 transport."""

from dataclasses import dataclass

from .gray_field import field_size


@dataclass(frozen=True, slots=True)
class FloatField:
    width: int
    height: int
    pixels: bytes

    def __post_init__(self):
        import numpy as np

        field_size(self.size)
        if not isinstance(self.pixels, bytes):
            raise TypeError("float32 field bytes must be immutable")
        if len(self.pixels) != self.width * self.height * 4:
            raise ValueError("float32 field length does not match dimensions")
        values = np.frombuffer(self.pixels, dtype="<f4")
        if not np.all(np.isfinite(values)) or np.any(values < 0) or np.any(values > 1):
            raise ValueError("float32 field samples must be finite and within 0..1")

    @property
    def size(self):
        return self.width, self.height

    def __array__(self, dtype=None, copy=None):
        import numpy as np

        array = np.frombuffer(self.pixels, dtype="<f4").reshape(self.height, self.width)
        if dtype is not None and np.dtype(dtype) != array.dtype:
            if copy is False:
                raise ValueError("dtype conversion requires copying float32 pixels")
            return array.astype(dtype)
        return array.copy() if copy else array

    @classmethod
    def from_array(cls, array):
        import numpy as np

        height, width = array.shape
        field_size((width, height))
        return cls(width, height, np.asarray(array, dtype="<f4").tobytes())
