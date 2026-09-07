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

    def render(self, display_list: GeneralPrefabDisplayList) -> Image.Image:
        image = Image.new("RGBA", display_list.size, (0, 0, 0, 0))

        def replay(target: Image.Image, ops: tuple[GeneralPrefabOp, ...]) -> None:
            draw = ImageDraw.Draw(target)

            def draw_rounded_rect(op: GeneralRoundedRectOp) -> None:
                rect = tuple(round(value) for value in op.rect) if op.round_coordinates else op.rect
                draw.rounded_rectangle(
                    rect,
                    radius=op.radius,
                    fill=op.fill,
                    outline=op.outline,
                    width=op.width,
                )

            def missing_resource(
                resource: str,
                policy: ResourcePolicy,
                fallback: GeneralRoundedRectOp | None,
            ) -> None:
                if policy == "required":
                    raise FileNotFoundError(f"required GeneralContentView resource is missing: {resource}")
                if policy == "fallback":
                    if fallback is None:  # pragma: no cover - dataclass validation rejects this
                        raise RuntimeError(f"missing fallback operation for GeneralContentView resource: {resource}")
                    draw_rounded_rect(fallback)

            for op in ops:
                if isinstance(op, GeneralSpriteOp):
                    pasted = self._sprite_paster(
                        target,
                        op.name,
                        op.rect,
                        tint=op.tint,
                        sliced_border=op.sliced_border,
                        resample=self._RESAMPLING[op.sampling],
                    )
                    if not pasted:
                        missing_resource(op.name, op.resource_policy, op.fallback)
                    continue
                if isinstance(op, GeneralRoundedRectOp):
                    draw_rounded_rect(op)
                    continue
                if isinstance(op, GeneralAssetImageOp):
                    path = Path(op.path) if op.path is not None else None
                    source = self._asset_loader(path) if self._asset_loader is not None else None
                    if source is None:
                        missing_resource(op.resource_key, op.resource_policy, op.fallback)
                        continue
                    left, top, right, bottom = op.rect
                    width = max(1, round(right - left))
                    height = max(1, round(bottom - top))
                    if op.fit == "cover":
                        scale = max(width / source.width, height / source.height)
                        resized = source.resize(
                            (
                                max(1, round(source.width * scale)),
                                max(1, round(source.height * scale)),
                            ),
                            self._RESAMPLING[op.sampling],
                        )
                        crop_left = round((resized.width - width) * op.align[0])
                        crop_top = round((resized.height - height) * op.align[1])
                        resized = resized.crop((crop_left, crop_top, crop_left + width, crop_top + height))
                    else:
                        resized = source.resize((width, height), self._RESAMPLING[op.sampling])
                    if op.clip_radius is not None:
                        mask = Image.new("L", resized.size, 0)
                        ImageDraw.Draw(mask).rounded_rectangle(
                            (0, 0, width - 1, height - 1),
                            radius=op.clip_radius,
                            fill=255,
                        )
                        resized.putalpha(ImageChops.multiply(resized.getchannel("A"), mask))
                    target.alpha_composite(resized, (round(left), round(top)))
                    continue
                if isinstance(op, GeneralViewportOp):
                    content = Image.new("RGBA", op.content_size, (0, 0, 0, 0))
                    replay(content, op.children)
                    viewport = content.crop((0, 0, op.viewport_size[0], op.viewport_size[1]))
                    target.alpha_composite(viewport, (round(op.offset[0]), round(op.offset[1])))
                    continue
                draw.text(
                    op.pos,
                    op.text,
                    font=self._font(op.font, op.size),
                    fill=op.fill,
                    anchor=op.anchor,
                )

        replay(image, display_list.ops)
        return image
