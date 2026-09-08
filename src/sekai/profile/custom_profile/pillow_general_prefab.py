"""Pillow-only replay adapter for the shared general_prefab display list."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol, TypeAlias

from PIL import Image, ImageChops, ImageDraw, ImageFont

from .general_prefab import (
    GeneralAssetImageOp,
    GeneralFontRef,
    GeneralPrefabDisplayList,
    GeneralPrefabOp,
    GeneralRoundedRectOp,
    GeneralSpriteChoiceOp,
    GeneralSpriteOp,
    GeneralTextMetrics,
    GeneralViewportOp,
    Rect,
    ResourcePolicy,
    Sampling,
    Tint,
)

PillowFontFactory: TypeAlias = Callable[[int, bool], ImageFont.ImageFont]
PillowAssetLoader: TypeAlias = Callable[[Path | None], Image.Image | None]


class PillowSpritePaster(Protocol):
    def __call__(
        self,
        image: Image.Image,
        name: str,
        rect: Rect,
        *,
        tint: Tint | None = None,
        sliced_border: tuple[int, int, int, int] | None = None,
        resample: Image.Resampling = Image.Resampling.LANCZOS,
    ) -> bool: ...


class PillowGeneralPrefabAdapter(GeneralTextMetrics):
    """Pillow metrics + display-list replay used by the compatibility renderer."""

    _RESAMPLING: Mapping[Sampling, Image.Resampling] = {
        "nearest": Image.Resampling.NEAREST,
        "bilinear": Image.Resampling.BILINEAR,
        "bicubic": Image.Resampling.BICUBIC,
        "lanczos": Image.Resampling.LANCZOS,
    }

    def __init__(
        self,
        font_factory: PillowFontFactory,
        sprite_paster: PillowSpritePaster,
        asset_loader: PillowAssetLoader | None = None,
    ) -> None:
        self._font_factory = font_factory
        self._sprite_paster = sprite_paster
        self._asset_loader = asset_loader
        self._fonts: dict[tuple[GeneralFontRef, int], ImageFont.ImageFont] = {}
        self._metric_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

    def _font(self, font: GeneralFontRef, size: int) -> ImageFont.ImageFont:
        key = (font, int(size))
        loaded = self._fonts.get(key)
        if loaded is None:
            loaded = self._font_factory(int(size), font.bold)
            self._fonts[key] = loaded
        return loaded

    def text_bbox(
        self,
        text: str,
        font: GeneralFontRef,
        size: int,
    ) -> tuple[float, float, float, float]:
        return self._metric_draw.textbbox((0, 0), text, font=self._font(font, size))

    @staticmethod
    def _draw_rounded_rect(draw: ImageDraw.ImageDraw, op: GeneralRoundedRectOp) -> None:
        rect = tuple(round(value) for value in op.rect) if op.round_coordinates else op.rect
        draw.rounded_rectangle(rect, radius=op.radius, fill=op.fill, outline=op.outline, width=op.width)

    def _missing_resource(
        self,
        draw: ImageDraw.ImageDraw,
        resource: str,
        policy: ResourcePolicy,
        fallback: GeneralRoundedRectOp | None,
    ) -> None:
        if policy == "required":
            raise FileNotFoundError(f"required GeneralContentView resource is missing: {resource}")
        if policy == "fallback":
            if fallback is None:  # pragma: no cover - dataclass validation rejects this
                raise RuntimeError(f"missing fallback operation for GeneralContentView resource: {resource}")
            self._draw_rounded_rect(draw, fallback)

    def _replay_sprite(self, target: Image.Image, draw: ImageDraw.ImageDraw, op: GeneralSpriteOp) -> None:
        pasted = self._sprite_paster(
            target,
            op.name,
            op.rect,
            tint=op.tint,
            sliced_border=op.sliced_border,
            resample=self._RESAMPLING[op.sampling],
        )
        if not pasted:
            self._missing_resource(draw, op.name, op.resource_policy, op.fallback)

    def _replay_sprite_choice(
        self,
        target: Image.Image,
        draw: ImageDraw.ImageDraw,
        op: GeneralSpriteChoiceOp,
    ) -> None:
        pasted = any(
            self._sprite_paster(
                target,
                name,
                op.rect,
                tint=op.tint,
                resample=self._RESAMPLING[op.sampling],
            )
            for name in op.names
        )
        if pasted or op.fallback_text is None:
            return
        fallback = op.fallback_text
        draw.text(
            fallback.pos,
            fallback.text,
            font=self._font(fallback.font, fallback.size),
            fill=fallback.fill,
            anchor=fallback.anchor,
        )

    def _asset_image(self, op: GeneralAssetImageOp, width: int, height: int) -> Image.Image | None:
        path = Path(op.path) if op.path is not None else None
        source = self._asset_loader(path) if self._asset_loader is not None else None
        if source is None:
            return None
        if op.fit != "cover":
            return source.resize((width, height), self._RESAMPLING[op.sampling])
        scale = max(width / source.width, height / source.height)
        resized = source.resize(
            (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
            self._RESAMPLING[op.sampling],
        )
        crop_left = round((resized.width - width) * op.align[0])
        crop_top = round((resized.height - height) * op.align[1])
        return resized.crop((crop_left, crop_top, crop_left + width, crop_top + height))

    @staticmethod
    def _clip_asset_image(image: Image.Image, width: int, height: int, radius: int) -> None:
        mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
        image.putalpha(ImageChops.multiply(image.getchannel("A"), mask))

    def _replay_asset(self, target: Image.Image, draw: ImageDraw.ImageDraw, op: GeneralAssetImageOp) -> None:
        left, top, right, bottom = op.rect
        width = max(1, round(right - left))
        height = max(1, round(bottom - top))
        resized = self._asset_image(op, width, height)
        if resized is None:
            self._missing_resource(draw, op.resource_key, op.resource_policy, op.fallback)
            return
        if op.clip_radius is not None:
            self._clip_asset_image(resized, width, height, op.clip_radius)
        target.alpha_composite(resized, (round(left), round(top)))

    def _replay_viewport(self, target: Image.Image, op: GeneralViewportOp) -> None:
        content = Image.new("RGBA", op.content_size, (0, 0, 0, 0))
        self._replay(content, op.children)
        viewport = content.crop((0, 0, op.viewport_size[0], op.viewport_size[1]))
        target.alpha_composite(viewport, (round(op.offset[0]), round(op.offset[1])))

    def _replay_op(self, target: Image.Image, draw: ImageDraw.ImageDraw, op: GeneralPrefabOp) -> None:
        if isinstance(op, GeneralSpriteOp):
            self._replay_sprite(target, draw, op)
        elif isinstance(op, GeneralSpriteChoiceOp):
            self._replay_sprite_choice(target, draw, op)
        elif isinstance(op, GeneralRoundedRectOp):
            self._draw_rounded_rect(draw, op)
        elif isinstance(op, GeneralAssetImageOp):
            self._replay_asset(target, draw, op)
        elif isinstance(op, GeneralViewportOp):
            self._replay_viewport(target, op)
        else:
            draw.text(op.pos, op.text, font=self._font(op.font, op.size), fill=op.fill, anchor=op.anchor)

    def _replay(self, target: Image.Image, ops: tuple[GeneralPrefabOp, ...]) -> None:
        draw = ImageDraw.Draw(target)
        for op in ops:
            self._replay_op(target, draw, op)

    def render(self, display_list: GeneralPrefabDisplayList) -> Image.Image:
        image = Image.new("RGBA", display_list.size, (0, 0, 0, 0))
        self._replay(image, display_list.ops)
        return image
