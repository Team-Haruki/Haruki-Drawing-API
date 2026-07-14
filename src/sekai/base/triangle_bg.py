"""The triangle background's layout — generated once, drawn by both backends.

Painter used to scatter the triangles straight from the **unseeded global** ``random`` while the
Rust renderer generated its own from a ``(width, height, hour)``-derived xorshift seed. Two
different PRNGs, two different normal-variate algorithms, two different rounding rules. Three
consequences, all of which cost real debugging time:

1. The two backends' backgrounds could **never** match, so the parity sweep had to keep a loose
   mean threshold on every canvas — a permanent blind spot exactly where drift hides.
2. Pillow did not reproduce *itself*: two renders of the same tree differed by ~12% of pixels, so
   the legacy-baseline harness once reported "everything drifted" when nothing had.
3. Rust was not reproducible either — its seed took ``hour`` at millisecond precision, so
   ``(hour * 1000) as u64`` rolled over every few seconds.

The layout is **data, not rendering**. It is generated here from an explicit, quantized seed; the
Pillow painter draws the list directly and ``IRPainter`` ships the same list to Skia on the
``tris`` field of the ``TriangleBg`` node. Neither backend rolls a die.

The palette is a different story and was never broken: both sides interpolate the same tables from
the same fractional hour, so it stays where it is.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
import random

# (hour, hue, saturation, lightness) — the custom-hue path's time-of-day modulation.
_TIME_COLORS: tuple[tuple[float, float, float, float], ...] = (
    (0, 0.57, 7.0, 0.1),
    (5, 0.57, 3.0, 0.2),
    (9, 0.57, 1.0, 0.8),
    (12, 0.57, 1.0, 1.0),
    (15, 0.57, 1.0, 0.8),
    (19, 0.57, 3.0, 0.2),
    (24, 0.57, 7.0, 0.1),
)

_PINK_TIME_PALETTES: tuple[dict, ...] = (
    {
        "hour": 0,
        "grad1": (128, 106, 170),
        "grad2": (194, 145, 210),
        "overlay1": (255, 194, 228, 60),
        "overlay2": (198, 184, 255, 42),
        "white_alpha": 72,
    },
    {
        "hour": 5,
        "grad1": (248, 218, 234),
        "grad2": (205, 214, 255),
        "overlay1": (255, 240, 246, 84),
        "overlay2": (255, 214, 236, 52),
        "white_alpha": 76,
    },
    {
        "hour": 9,
        "grad1": (236, 208, 228),
        "grad2": (172, 205, 255),
        "overlay1": (255, 244, 248, 76),
        "overlay2": (226, 221, 255, 52),
        "white_alpha": 72,
    },
    {
        "hour": 12,
        "grad1": (230, 198, 224),
        "grad2": (160, 198, 255),
        "overlay1": (255, 246, 249, 72),
        "overlay2": (214, 219, 255, 50),
        "white_alpha": 70,
    },
    {
        "hour": 15,
        "grad1": (242, 186, 216),
        "grad2": (173, 186, 242),
        "overlay1": (255, 220, 236, 88),
        "overlay2": (255, 192, 224, 54),
        "white_alpha": 64,
    },
    {
        "hour": 19,
        "grad1": (176, 132, 190),
        "grad2": (146, 156, 226),
        "overlay1": (255, 194, 224, 62),
        "overlay2": (213, 188, 255, 40),
        "white_alpha": 58,
    },
    {
        "hour": 24,
        "grad1": (128, 106, 170),
        "grad2": (194, 145, 210),
        "overlay1": (255, 194, 228, 60),
        "overlay2": (198, 184, 255, 42),
        "white_alpha": 72,
    },
)

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class Triangle:
    """One scattered triangle. ``x``/``y`` are subpixel on purpose: Skia draws the path at float
    coordinates, so Painter has to as well or the two backends land the shape up to half a pixel
    apart."""

    x: float
    y: float
    rot: float
    size: int
    color: RGBA
    type: int


@dataclass(frozen=True, slots=True)
class TriangleBgSpec:
    grad1: RGBA
    grad2: RGBA
    overlay1: RGBA
    overlay2: RGBA
    white_alpha: int
    triangles: tuple[Triangle, ...]


def background_hour() -> float:
    """The fractional hour both backends key the background on.

    ``HARUKI_BG_TEST_HOUR`` pins it, which is what makes a background reproducible across processes
    (the parity harnesses rely on this)."""
    override = os.getenv("HARUKI_BG_TEST_HOUR")
    if override is not None:
        try:
            return max(0.0, min(23.999, float(override)))
        except ValueError:
            pass
    now = datetime.now()
    return now.hour + now.minute / 60 + now.second / 3600


def _lerp_tuple(c1, c2, t: float) -> tuple[int, ...]:
    return tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(len(c1)))


def _time_color(hour: float) -> tuple[float, float, float]:
    """(hue, saturation, lightness) for the custom-hue palette at this hour."""
    if hour < _TIME_COLORS[0][0]:
        return _TIME_COLORS[0][1:]
    if hour >= _TIME_COLORS[-1][0]:
        return _TIME_COLORS[-1][1:]
    for i in range(len(_TIME_COLORS) - 1):
        h1, hue1, sat1, light1 = _TIME_COLORS[i]
        h2, hue2, sat2, light2 = _TIME_COLORS[i + 1]
        if h1 <= hour < h2:
            x = (hour - h1) / (h2 - h1)
            return (
                hue1 + (hue2 - hue1) * x,
                sat1 + (sat2 - sat1) * x,
                light1 + (light2 - light1) * x,
            )
    return _TIME_COLORS[-1][1:]


def _pink_palette(hour: float) -> dict:
    if hour < _PINK_TIME_PALETTES[0]["hour"]:
        return _PINK_TIME_PALETTES[0]
    if hour >= _PINK_TIME_PALETTES[-1]["hour"]:
        return _PINK_TIME_PALETTES[-1]
    for i in range(len(_PINK_TIME_PALETTES) - 1):
        cur, nxt = _PINK_TIME_PALETTES[i], _PINK_TIME_PALETTES[i + 1]
        if cur["hour"] <= hour <= nxt["hour"]:
            span = nxt["hour"] - cur["hour"]
            x = 0.0 if span == 0 else (hour - cur["hour"]) / span
            return {
                "grad1": _lerp_tuple(cur["grad1"], nxt["grad1"], x),
                "grad2": _lerp_tuple(cur["grad2"], nxt["grad2"], x),
                "overlay1": _lerp_tuple(cur["overlay1"], nxt["overlay1"], x),
                "overlay2": _lerp_tuple(cur["overlay2"], nxt["overlay2"], x),
                "white_alpha": int(cur["white_alpha"] * (1 - x) + nxt["white_alpha"] * x),
            }
    return _PINK_TIME_PALETTES[-1]


def _brighten(color: RGB, amount: float = 0.22) -> RGB:
    return tuple(min(255, int(c + (255 - c) * amount)) for c in color)


def _mix(c1: RGB, c2: RGB, ratio: float) -> RGB:
    return tuple(int(c1[i] * (1 - ratio) + c2[i] * ratio) for i in range(3))


def gradient_points(width: int, height: int):
    """Endpoints of the two linear gradients, in normalized canvas coordinates."""
    aspect = width / max(height, 1)
    wide_bias = max(0.0, min(0.2, (aspect - 1.0) * 0.12))
    tall_bias = max(0.0, min(0.16, (1.0 - aspect) * 0.16))
    return (
        (0.02 + tall_bias * 0.3, 0.96 - wide_bias),
        (0.98 - tall_bias * 0.3, 0.08 + wide_bias * 0.8),
        (0.04 + tall_bias * 0.2, 0.06 + wide_bias * 0.3),
        (0.96 - tall_bias * 0.2, 0.94 - wide_bias * 0.5),
    )


def triangle_bg_seed(
    width: int, height: int, hour: float, time_color: bool, main_hue: float, size_fixed_rate: float
) -> int:
    """A stable seed for this background's scatter.

    ``hour`` is quantized to the whole hour on purpose. The palette keeps the *fractional* hour and
    goes on shifting smoothly with the clock; only the layout is pinned, so the same page renders
    the same triangles for an hour at a time. That is what makes a render reproducible — and what
    lets a raster cache key on the seed at all. (The old Rust seed took the fractional hour at
    millisecond precision, so it changed roughly every 3.6 seconds.)

    blake2b rather than ``hash()``: PYTHONHASHSEED randomizes ``hash()`` of anything with a string
    in it per process, which is precisely the non-reproducibility we are removing.
    """
    material = f"{width}x{height}|{int(hour) % 24}|{int(bool(time_color))}|{main_hue:.6f}|{size_fixed_rate:.6f}"
    return int.from_bytes(hashlib.blake2b(material.encode(), digest_size=8).digest(), "big")


def build_triangle_bg(
    width: int,
    height: int,
    hour: float,
    time_color: bool,
    main_hue: float | None,
    size_fixed_rate: float,
) -> TriangleBgSpec:
    """Resolve the palette and scatter the triangles. Pure: same arguments, same output, forever."""
    main_hue = 0.0 if main_hue is None else float(main_hue)
    size_fixed_rate = float(size_fixed_rate or 0.0)

    if time_color:
        palette = _pink_palette(hour)
        _, sat_mul, light_mul = _time_color(hour)
        grad1: RGBA = (*palette["grad1"], 255)
        grad2: RGBA = (*palette["grad2"], 255)
        overlay1: RGBA = tuple(palette["overlay1"])
        overlay2: RGBA = tuple(palette["overlay2"])
        white_alpha = palette["white_alpha"]
        mid = _mix(palette["grad1"], palette["grad2"], 0.5)
        preset_colors = [
            _brighten(_mix(palette["grad1"], (255, 206, 232), 0.72), 0.20),
            _brighten(_mix(mid, (238, 214, 255), 0.68), 0.18),
            _brighten(_mix(palette["grad2"], (208, 232, 255), 0.66), 0.20),
            _brighten(_mix(mid, (255, 228, 176), 0.56), 0.18),
        ]
    else:
        sat_mul = light_mul = 1.0

        def h2c(hue: float, sat: float, light: float, alpha: int = 255) -> RGBA:
            hue = (hue + 1.0) % 1.0
            r, g, b = colorsys.hls_to_rgb(hue, light * light_mul, sat * sat_mul)
            return (int(255 * r), int(255 * g), int(255 * b), alpha)

        ofs = 0.025
        grad1 = h2c(main_hue, 0.5, 1.0)
        grad2 = h2c(main_hue + ofs, 0.9, 0.5)
        overlay1 = h2c(main_hue, 0.9, 0.7, 100)
        overlay2 = h2c(main_hue - ofs, 0.5, 0.5, 100)
        white_alpha = 100
        preset_colors = [_brighten(c) for c in ((255, 189, 246), (183, 246, 255), (255, 247, 146))]

    rng = random.Random(triangle_bg_seed(width, height, hour, time_color, main_hue, size_fixed_rate))

    w, h = width, height
    factor = min(w, h) / 2048 * 1.5
    size_factor = 1.0 + (factor - 1.0) * (1.0 - size_fixed_rate)
    dense_factor = 1.0 + (factor * factor - 1.0) * size_fixed_rate
    aspect_density_boost = min(1.55, max(1.15, (w / max(h, 1)) ** 0.22))
    aspect = w / max(h, 1)
    wide_shift = min(0.12, max(0.0, (aspect - 1.0) * 0.08))

    triangles: list[Triangle] = []

    def scatter(num: int, sz: tuple[float, float]) -> None:
        for _ in range(num):
            if rng.random() < 0.78:
                edge = rng.choices(
                    ("left", "right", "top", "bottom"),
                    weights=(0.9, 0.95 - wide_shift * 1.8, 1.18 + wide_shift * 1.6, 0.72 - wide_shift * 1.2),
                    k=1,
                )[0]
                if edge == "left":
                    x = rng.uniform(-0.04 * w, 0.18 * w)
                    y = rng.uniform(0, h)
                elif edge == "right":
                    x = rng.uniform((0.82 - wide_shift) * w, 1.03 * w)
                    y = rng.uniform(0, h)
                elif edge == "top":
                    x = rng.uniform(0, w)
                    y = rng.uniform(-0.04 * h, (0.20 + wide_shift * 0.5) * h)
                else:
                    x = rng.uniform(0, w)
                    y = rng.uniform((0.80 - wide_shift * 0.8) * h, 1.03 * h)
            else:
                x = rng.uniform(0.12 * w, 0.88 * w)
                y = rng.uniform(0.12 * h, 0.88 * h)
            if x < 0 or x >= w or y < 0 or y >= h:
                continue
            rot = rng.uniform(0, 360)
            size = max(1, min(1000, int(rng.normalvariate(sz[0], sz[1]))))
            dist = ((x - w // 2) / w * 2) ** 2 + ((y - h // 2) / h * 2) ** 2
            size = int(size * max(0.28, dist))

            size_alpha_factor, std_size_lower, std_size_upper = 1.0, 64 * size_factor, 128 * size_factor
            if size < std_size_lower:
                size_alpha_factor = size / std_size_lower
            if size > std_size_upper:
                size_alpha_factor = 1.0 - (size - std_size_upper * 1.5) / (std_size_upper * 1.5)
            alpha = int(rng.normalvariate(122, 138) * max(0, min(1.5, size_alpha_factor) * (light_mul**0.5)))
            if rng.random() < 0.05 and size > std_size_lower:
                alpha = 255
            if alpha <= 34:
                continue
            alpha = max(0, min(255, alpha))
            color = rng.choice(preset_colors)
            tri_type = rng.choice([0, 1, 1, 1, 2, 2])
            triangles.append(Triangle(x=x, y=y, rot=rot, size=size, color=(*color, alpha), type=tri_type))

    scatter(int(28 * dense_factor * aspect_density_boost), (128 * size_factor, 16 * size_factor))
    scatter(int(280 * dense_factor * aspect_density_boost), (64 * size_factor, 16 * size_factor))

    return TriangleBgSpec(
        grad1=grad1,
        grad2=grad2,
        overlay1=overlay1,
        overlay2=overlay2,
        white_alpha=white_alpha,
        triangles=tuple(triangles),
    )
