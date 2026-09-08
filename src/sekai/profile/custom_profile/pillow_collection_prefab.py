"""Development-only Pillow replay of the collection display list."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypeAlias

from PIL import Image, ImageDraw, ImageFont

from .collection_prefab import OmikujiAssetOp, OmikujiDisplayList, OmikujiRectOp, OmikujiTextOp, Sampling

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

    def _draw_asset(self, image: Image.Image, op: OmikujiAssetOp) -> None:
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

    def _draw_text(self, image: Image.Image, draw: ImageDraw.ImageDraw, op: OmikujiTextOp) -> None:
        font = self._font(op.size, op.decorative)
        if abs(op.rotation) < 1.0e-6:
            draw.text(op.pos, op.text, font=font, fill=op.fill, anchor=op.anchor)
            return
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

    def render(self, display_list: OmikujiDisplayList) -> Image.Image:
        image = Image.new("RGBA", display_list.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for op in display_list.ops:
            if isinstance(op, OmikujiAssetOp):
                self._draw_asset(image, op)
                continue
            if isinstance(op, OmikujiRectOp):
                draw.rectangle(op.rect, fill=op.fill)
                continue
            self._draw_text(image, draw, op)
        return image
