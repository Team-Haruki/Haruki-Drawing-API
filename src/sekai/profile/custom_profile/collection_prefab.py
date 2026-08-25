"""Renderer-neutral display list for dynamic custom-profile collection prefabs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from PIL import Image, ImageDraw, ImageFont

Color = tuple[int, int, int, int]
Rect = tuple[float, float, float, float]
Sampling = Literal["nearest", "bilinear", "bicubic", "lanczos"]
AssetPath = str | Path

OMIKUJI_RESULT_NATIVE_SIZE = (1480.0, 490.0)
OMIKUJI_UNIT_COLORS: Mapping[str, Color] = {
    "piapro": (51, 204, 187, 255),
    "light_sound": (68, 85, 221, 255),
    "idol": (136, 221, 68, 255),
    "street": (238, 17, 102, 255),
    "theme_park": (255, 153, 0, 255),
    "school_refusal": (136, 68, 153, 255),
}


@dataclass(frozen=True, slots=True)
class OmikujiAssetOp:
    resource_key: str
    path: AssetPath
    rect: Rect
    sampling: Sampling = "lanczos"
    blend: Literal["src", "src_over"] = "src_over"


@dataclass(frozen=True, slots=True)
class OmikujiRectOp:
    rect: Rect
    fill: Color


@dataclass(frozen=True, slots=True)
class OmikujiTextOp:
    text: str
    pos: tuple[float, float]
    size: int
    fill: Color
    anchor: str = "mm"
    decorative: bool = False
    rotation: float = 0.0


OmikujiOp: TypeAlias = OmikujiAssetOp | OmikujiRectOp | OmikujiTextOp


@dataclass(frozen=True, slots=True)
class OmikujiDisplayList:
    size: tuple[int, int]
    ops: tuple[OmikujiOp, ...]


def _append_vertical_line(
    ops: list[OmikujiOp],
    x: float,
    y: float,
    text: str,
    size: int,
    fill: Color,
    *,
    step: float,
) -> None:
    cursor_y = y
    rotate_chars = {"、", "。", "，", "．", "・", "：", "；", "！", "？", "ー"}
    small_kana = set("ぁぃぅぇぉっゃゅょァィゥェォッャュョ")
    for char in str(text or ""):
        if char in {" ", "\u3000"}:
            cursor_y += step * 0.5
            continue
        if char in rotate_chars:
            ops.append(
                OmikujiTextOp(
                    char,
                    (x, cursor_y + step * 0.28),
                    size,
                    fill,
                    rotation=90.0,
                )
            )
        else:
            ops.append(
                OmikujiTextOp(
                    char,
                    (
                        x - step * 0.08 if char in small_kana else x,
                        cursor_y + step * 0.16 if char in small_kana else cursor_y,
                    ),
                    size,
                    fill,
                )
            )
        cursor_y += step


def build_omikuji_display_list(
    omikuji: Mapping[str, object],
    *,
    background_path: AssetPath,
    background_size: tuple[int, int],
    fortune_path: AssetPath,
    fortune_size: tuple[int, int],
) -> OmikujiDisplayList:
    """Build the complete ``CollectionCustomPrefabContentView`` result card."""

    width, height = (int(background_size[0]), int(background_size[1]))
    fortune_w, fortune_h = (int(fortune_size[0]), int(fortune_size[1]))
    if width <= 0 or height <= 0 or fortune_w <= 0 or fortune_h <= 0:
        raise ValueError("omikuji display-list assets need positive dimensions")

    ops: list[OmikujiOp] = [
        OmikujiAssetOp("background", background_path, (0, 0, width, height), sampling="nearest", blend="src")
    ]
    target_h = max(1, round(height * 300.0 / 490.0))
    target_w = max(1, round(fortune_w * target_h / fortune_h))
    fortune_left = round(width * 1309.0 / 1480.0)
    fortune_top = round(height * 89.0 / 490.0)
    ops.append(
        OmikujiAssetOp(
            "fortune",
            fortune_path,
            (fortune_left, fortune_top, fortune_left + target_w, fortune_top + target_h),
            sampling="lanczos",
        )
    )

    text_fill = (79, 79, 79, 255)
    summary_size = round(height * 36.0 / 490.0)
    summary_lines = [line for line in str(omikuji.get("summary", "") or "").splitlines() if line]
    for index, line in enumerate(summary_lines):
        _append_vertical_line(
            ops,
            width * 1251.0 / 1480.0 - index * width * 44.0 / 1480.0,
            height * 49.0 / 490.0,
            line,
            summary_size,
            text_fill,
            step=height * 29.5 / 490.0,
        )

    rows = (
        (str(omikuji.get("title3", "") or ""), str(omikuji.get("description3", "") or "")),
        (str(omikuji.get("title2", "") or ""), str(omikuji.get("description2", "") or "")),
        (str(omikuji.get("title1", "") or ""), str(omikuji.get("description1", "") or "")),
    )
    accent = OMIKUJI_UNIT_COLORS.get(str(omikuji.get("unit", "") or ""), (76, 181, 210, 255))
    title_size = round(height * 40.0 / 490.0)
    value_size = round(height * 30.0 / 490.0)
    title_lefts = (width * 430.0 / 1480.0, width * 584.0 / 1480.0, width * 736.0 / 1480.0)
    title_top = height * 31.0 / 490.0
    title_w = width * 44.0 / 1480.0
    title_h = height * 94.0 / 490.0
    for (title, value), title_left in zip(rows, title_lefts, strict=True):
        if not title and not value:
            continue
        ops.append(
            OmikujiRectOp(
                (
                    round(title_left),
                    round(title_top),
                    round(title_left + title_w),
                    round(title_top + title_h),
                ),
                accent,
            )
        )
        clean_title = title.replace(" ", "")
        if clean_title:
            _append_vertical_line(
                ops,
                title_left + title_w / 2.0,
                title_top + height * 27.0 / 490.0,
                clean_title,
                title_size,
                (255, 255, 255, 255),
                step=height * 39.0 / 490.0,
            )
        if value:
            _append_vertical_line(
                ops,
                title_left - width * 40.0 / 1480.0,
                height * 55.0 / 490.0,
                value,
                value_size,
                text_fill,
                step=height * 25.0 / 490.0,
            )
    return OmikujiDisplayList((width, height), tuple(ops))


OmikujiFontFactory: TypeAlias = Callable[[int, bool], ImageFont.ImageFont]
OmikujiAssetLoader: TypeAlias = Callable[[Path], Image.Image | None]


class PillowOmikujiAdapter:
    """Compatibility replay for the shared omikuji display list."""

    _RESAMPLING: Mapping[Sampling, Image.Resampling] = {
        "nearest": Image.Resampling.NEAREST,
        "bilinear": Image.Resampling.BILINEAR,
        "bicubic": Image.Resampling.BICUBIC,
        "lanczos": Image.Resampling.LANCZOS,
    }

    def __init__(self, font_factory: OmikujiFontFactory, asset_loader: OmikujiAssetLoader) -> None:
        self._font_factory = font_factory
        self._asset_loader = asset_loader
        self._fonts: dict[tuple[int, bool], ImageFont.ImageFont] = {}

    def _font(self, size: int, decorative: bool) -> ImageFont.ImageFont:
        key = (int(size), bool(decorative))
        font = self._fonts.get(key)
        if font is None:
            font = self._font_factory(*key)
            self._fonts[key] = font
        return font

    def render(self, display_list: OmikujiDisplayList) -> Image.Image:
        image = Image.new("RGBA", display_list.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for op in display_list.ops:
            if isinstance(op, OmikujiAssetOp):
                source = self._asset_loader(Path(op.path))
                if source is None:
                    raise FileNotFoundError(f"required omikuji asset is missing: {op.resource_key}")
                left, top, right, bottom = op.rect
                target_size = (max(1, round(right - left)), max(1, round(bottom - top)))
                if source.size != target_size:
                    source = source.resize(target_size, self._RESAMPLING[op.sampling])
                pos = (round(left), round(top))
                if op.blend == "src":
                    image.paste(source, pos)
                else:
                    image.alpha_composite(source, pos)
                continue
            if isinstance(op, OmikujiRectOp):
                draw.rectangle(op.rect, fill=op.fill)
                continue

            font = self._font(op.size, op.decorative)
            if abs(op.rotation) < 1.0e-6:
                draw.text(op.pos, op.text, font=font, fill=op.fill, anchor=op.anchor)
                continue
            bbox = draw.textbbox((0, 0), op.text, font=font)
            glyph = Image.new(
                "RGBA",
                (max(1, bbox[2] - bbox[0] + 8), max(1, bbox[3] - bbox[1] + 8)),
                (0, 0, 0, 0),
            )
            ImageDraw.Draw(glyph).text((4 - bbox[0], 4 - bbox[1]), op.text, font=font, fill=op.fill)
            glyph = glyph.rotate(op.rotation, expand=True)
            image.alpha_composite(
                glyph,
                (round(op.pos[0] - glyph.width / 2.0), round(op.pos[1] - glyph.height / 2.0)),
            )
        return image
