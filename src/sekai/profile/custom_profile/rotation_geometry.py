"""Expanded center rotation geometry shared with legacy TMP glyph placement.

Matches Pillow 12.3 Image.rotate's reverse matrix and extent rounding. This
module computes geometry only; the Pillow license is in docs/licenses/pillow.txt.
"""

from dataclasses import dataclass
import math

from .gray_field import field_size


@dataclass(frozen=True, slots=True)
class ExpandedRotation:
    size: tuple[int, int]
    inverse: tuple[float, ...]


def expanded_rotation(size: tuple[int, int], angle: float) -> ExpandedRotation:
    w, h = field_size(size)
    if not math.isfinite(angle):
        raise ValueError("rotation angle must be finite")
    angle %= 360.0
    theta = -math.radians(angle)
    a, b = round(math.cos(theta), 15), round(math.sin(theta), 15)
    d, e = round(-math.sin(theta), 15), a
    c, f = a * (-w / 2) + b * (-h / 2) + w / 2, d * (-w / 2) + e * (-h / 2) + h / 2
    if angle in (0, 180):
        nw, nh = w, h
    elif angle in (90, 270):
        nw, nh = h, w
    else:
        xs, ys = zip(*((a * x + b * y + c, d * x + e * y + f) for x, y in ((0, 0), (w, 0), (w, h), (0, h))))
        nw, nh = math.ceil(max(xs)) - math.floor(min(xs)), math.ceil(max(ys)) - math.floor(min(ys))
    tx, ty = -(nw - w) / 2.0, -(nh - h) / 2.0
    c, f = a * tx + b * ty + c, d * tx + e * ty + f
    return ExpandedRotation(field_size((nw, nh)), (a, b, c, d, e, f))
