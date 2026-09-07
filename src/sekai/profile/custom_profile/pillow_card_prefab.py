"""Pillow-only replay adapter for the shared card_prefab display list."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, Protocol, TypeAlias

from PIL import Image, ImageChops, ImageDraw, ImageFont

from .card_prefab import (
    AssetPath,
    CardAlphaMaskOp,
    CardCoverArtOp,
    CardDisplayList,
    CardFontRef,
    CardOp,
    CardRectOp,
    CardSpriteOp,
    CardTextOp,
    Rect,
    Sampling,
)

PillowAssetLoader: TypeAlias = Callable[[Path | None], Image.Image | None]
PillowFontFactory: TypeAlias = Callable[[int, bool], ImageFont.ImageFont]
PillowSpriteLoader: TypeAlias = Callable[[str], Image.Image | None]


class PillowSpritePaster(Protocol):
    def __call__(
        self,
        image: Image.Image,
        name: str,
        rect: Rect,
        *,
        resample: Image.Resampling = Image.Resampling.LANCZOS,
    ) -> bool: ...


class PillowCardAdapter:
    """Replay a ``CardDisplayList`` with the legacy Pillow pixel pipeline."""

    _RESAMPLING: ClassVar[dict[Sampling, Image.Resampling]] = {
        "nearest": Image.Resampling.NEAREST,
        "bilinear": Image.Resampling.BILINEAR,
        "bicubic": Image.Resampling.BICUBIC,
        "lanczos": Image.Resampling.LANCZOS,
    }

    def __init__(
        self,
        font_factory: PillowFontFactory,
        sprite_paster: PillowSpritePaster,
        sprite_loader: PillowSpriteLoader,
        asset_loader: PillowAssetLoader,
    ) -> None:
        self._font_factory = font_factory
        self._sprite_paster = sprite_paster
        self._sprite_loader = sprite_loader
        self._asset_loader = asset_loader
        self._fonts: dict[tuple[CardFontRef, int], ImageFont.ImageFont] = {}

    def _font(self, ref: CardFontRef, size: int) -> ImageFont.ImageFont:
        key = (ref, int(size))
        font = self._fonts.get(key)
        if font is None:
            font = self._font_factory(int(size), ref.bold)
            self._fonts[key] = font
        return font

    def _load_asset(self, path: AssetPath | None) -> Image.Image | None:
        return self._asset_loader(Path(path)) if path is not None else None

    @staticmethod
    def _resize_cover_aligned(
        source: Image.Image,
        target_size: tuple[float, float],
        align: tuple[float, float],
        resample: Image.Resampling,
    ) -> Image.Image:
        target_w = max(1, round(target_size[0]))
        target_h = max(1, round(target_size[1]))
        scale = max(target_w / source.width, target_h / source.height)
        resized = source.resize(
            (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
            resample,
        )
        left = round((resized.width - target_w) * align[0])
        top = round((resized.height - target_h) * align[1])
        return resized.crop((left, top, left + target_w, top + target_h))

    def _paste_sprite(self, image: Image.Image, op: CardSpriteOp) -> None:
        resample = self._RESAMPLING[op.sampling]
        if self._sprite_paster(image, op.resource.name, op.rect, resample=resample):
            return
        fallback = self._load_asset(op.resource.fallback_path)
        if fallback is not None:
            left, top, right, bottom = op.rect
            resized = fallback.resize(
                (max(1, round(right - left)), max(1, round(bottom - top))),
                resample,
            )
            image.alpha_composite(resized, (round(left), round(top)))
            return
        if op.resource.resource_policy == "required":
            raise FileNotFoundError(f"required card sprite is missing: {op.resource.name}")

    def _apply_mask(self, image: Image.Image, op: CardAlphaMaskOp) -> Image.Image:
        mask_sprite = self._sprite_loader(op.resource.name)
        if mask_sprite is None:
            mask_sprite = self._load_asset(op.resource.path)
        if mask_sprite is None:
            mask = Image.new("L", image.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, image.width, image.height),
                radius=max(1, round(min(image.width, image.height) * op.fallback_radius_ratio)),
                fill=255,
            )
        else:
            mask = mask_sprite.getchannel("A").resize(image.size, self._RESAMPLING[op.sampling])
        masked = image.copy()
        masked.putalpha(ImageChops.multiply(masked.getchannel("A"), mask))
        return masked

    def apply_ops(self, image: Image.Image, ops: tuple[CardOp, ...]) -> Image.Image:
        for op in ops:
            if isinstance(op, CardCoverArtOp):
                source = self._load_asset(op.path)
                if source is None:
                    raise FileNotFoundError(f"required card art is missing: {op.path}")
                art = self._resize_cover_aligned(
                    source,
                    op.cover_size,
                    op.cover_align,
                    self._RESAMPLING[op.sampling],
                )
                crop_left = max(0, round((art.width - image.width) * op.crop_align[0]))
                crop_top = max(0, round((art.height - image.height) * op.crop_align[1]))
                cropped = art.crop((crop_left, crop_top, crop_left + image.width, crop_top + image.height))
                if op.blend == "src":
                    image.paste(cropped, (0, 0))
                else:
                    image.alpha_composite(cropped)
                continue
            if isinstance(op, CardRectOp):
                rect = tuple(round(value) for value in op.rect) if op.round_coordinates else op.rect

                def draw_rect(draw: ImageDraw.ImageDraw) -> None:
                    if op.radius > 0:
                        draw.rounded_rectangle(
                            rect,
                            radius=op.radius,
                            fill=op.fill,
                            outline=op.outline,
                            width=op.width,
                        )
                    else:
                        draw.rectangle(
                            rect,
                            fill=op.fill,
                            outline=op.outline,
                            width=op.width,
                        )

                if op.blend == "src":
                    draw_rect(ImageDraw.Draw(image))
                else:
                    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
                    draw_rect(ImageDraw.Draw(overlay))
                    image.alpha_composite(overlay)
                continue
            if isinstance(op, CardTextOp):
                ImageDraw.Draw(image).text(
                    op.pos,
                    op.text,
                    font=self._font(op.font, op.size),
                    fill=op.fill,
                    anchor=op.anchor,
                )
                continue
            if isinstance(op, CardSpriteOp):
                self._paste_sprite(image, op)
                continue
            image = self._apply_mask(image, op)
        return image

    def render(self, display_list: CardDisplayList) -> Image.Image:
        image = self.apply_ops(
            Image.new("RGBA", display_list.size, (0, 0, 0, 0)),
            display_list.ops,
        )
        if display_list.render_size is not None:
            return image.resize(
                display_list.render_size,
                self._RESAMPLING[display_list.final_sampling],
            )
        return image
