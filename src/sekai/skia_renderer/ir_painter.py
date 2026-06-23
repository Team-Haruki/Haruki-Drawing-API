"""IRPainter — a ``Painter``-API-compatible shim that records Render IR instead of
rasterizing with Pillow.

The plot.py widget tree (and direct-Painter drawers) render by calling a small set of
``Painter`` primitives. Subclassing ``Painter`` and overriding those primitives lets the
SAME widget tree emit a declarative IR scene (built via :class:`IRBuilder`) that the Rust
``render_scene`` interpreter draws — without touching any drawer. The widgets resolve their
own layout to concrete coordinates before calling us, so we only translate each call.

Coordinates: widgets pass positions in the current region's local space; we flatten to
absolute by adding ``self.offset`` (the same offset ``Painter.add_operation`` would capture).

Runtime images (``paste``) arrive as in-memory ``PIL.Image`` objects with no asset path, so
they are encoded and shipped as ``mem:<key>`` images (see the renderer's mem-image support).

Anything not yet expressible raises :class:`SkiaUnsupported`; callers catch it and fall back
to the Pillow path, so coverage can grow incrementally without breaking endpoints.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image

from src.sekai.base.painter import (
    AdaptiveTextColor,
    FontDesc,
    LinearGradient,
    Painter,
    RadialGradient,
)
from src.sekai.skia_renderer.ir_builder import IRBuilder, adaptive_color, image_shadow, linear_gradient, radial_gradient


class SkiaUnsupported(RuntimeError):
    """Raised when a Painter op cannot be faithfully expressed as IR (→ Pillow fallback)."""


def _rgba(color: Any) -> tuple[int, int, int, int]:
    c = tuple(int(v) for v in color)
    if len(c) == 3:
        return (c[0], c[1], c[2], 255)
    return (c[0], c[1], c[2], c[3])


class IRPainter(Painter):
    def __init__(
        self,
        size: tuple[int, int],
        *,
        assets_base_dir: str,
        font_dir: str,
        default_font: str,
        bold_font: str,
        heavy_font: str | None = None,
        emoji_font: str | None = None,
        bg_hour: float = 12.0,
        export_format: str = "png",
        jpg_quality: int = 90,
    ) -> None:
        super().__init__(size=size)
        self._b = IRBuilder(
            size[0], size[1], assets_base_dir=assets_base_dir, font_dir=font_dir,
            default_font=default_font, bold_font=bold_font, heavy_font=heavy_font,
            emoji_font=emoji_font, export_format=export_format, jpg_quality=jpg_quality,
        )
        self._default_name = default_font
        self._bold_name = bold_font
        self._heavy_name = heavy_font
        self._bg_hour = bg_hour
        self._mem_images: dict[str, bytes] = {}
        self._mem_by_id: dict[int, str] = {}

    # ---- output ----

    def build_scene(self) -> tuple[dict, dict[str, bytes]]:
        return self._b.build(), self._mem_images

    # ---- region model (translation only; restore guards the missing self.img) ----

    def restore_region(self, depth: int = 1):
        if not self.region_stack:
            self.offset = (0, 0)
            self.size = (self._b.width, self._b.height)
            self.w, self.h = self.size
        else:
            self.offset, self.size = self.region_stack.pop()
            self.w, self.h = self.size
        if depth > 1:
            return self.restore_region(depth - 1)
        return self

    # ---- helpers ----

    def _abs(self, pos) -> tuple[float, float]:
        return (pos[0] + self.offset[0], pos[1] + self.offset[1])

    def _font(self, font) -> tuple[str, float, str | None]:
        """Map a FontDesc / PIL Font to (role, size, font_name)."""
        if isinstance(font, FontDesc):
            path, size = font.path, font.size
        else:
            path, size = getattr(font, "path", None), getattr(font, "size", None)
            if path is None or size is None:
                raise SkiaUnsupported("text font is not a FontDesc and lacks path/size")
            import os

            path = os.path.splitext(os.path.basename(path))[0]
        if path == self._default_name:
            return "default", size, None
        if path == self._bold_name:
            return "bold", size, None
        if self._heavy_name and path == self._heavy_name:
            return "heavy", size, None
        # Arbitrary font: register it in the scene's extra map and address by name.
        self._b._fonts.setdefault("extra", {})[path] = path
        return "default", size, path

    def _mem_image(self, img: Image.Image) -> str:
        """Encode a runtime PIL image once and return its ``mem:<key>`` reference."""
        key = self._mem_by_id.get(id(img))
        if key is None:
            key = f"m{len(self._mem_images)}"
            buf = BytesIO()
            img.convert("RGBA").save(buf, "PNG")
            self._mem_images[key] = buf.getvalue()
            self._mem_by_id[id(img)] = key
        return f"mem:{key}"

    def _fill(self, fill, apos, size):
        """Map a Painter fill (Color / LinearGradient / RadialGradient) to an IR fill."""
        if isinstance(fill, LinearGradient):
            return linear_gradient(
                _rgba(fill.c1), _rgba(fill.c2),
                (apos[0] + fill.p1[0] * size[0], apos[1] + fill.p1[1] * size[1]),
                (apos[0] + fill.p2[0] * size[0], apos[1] + fill.p2[1] * size[1]),
                method=fill.method,
            )
        if isinstance(fill, RadialGradient):
            return radial_gradient(
                _rgba(fill.c1), _rgba(fill.c2),
                (apos[0] + fill.center[0] * size[0], apos[1] + fill.center[1] * size[1]),
                radius_px=float(fill.radius),
            )
        return _rgba(fill)

    # ---- drawing primitives (emit IR; ignore exclude_on_hash) ----

    def text(self, text, pos, font, fill=(0, 0, 0, 255), align="left", exclude_on_hash=False):
        role, size, font_name = self._font(font)
        apos = self._abs(pos)
        adaptive = None
        if isinstance(fill, AdaptiveTextColor):
            adaptive = adaptive_color(_rgba(fill.light), _rgba(fill.dark), fill.threshold)
            fillval: Any = (0, 0, 0, 255)
        elif isinstance(fill, (LinearGradient, RadialGradient)):
            # Gradient text needs glyph-bbox endpoint mapping; defer to Pillow for now.
            raise SkiaUnsupported("gradient text fill not yet mapped")
        else:
            fillval = _rgba(fill)
        self._b.text(text, apos, role, size, align=align, baseline="cjk_top",
                     fill=fillval, adaptive=adaptive, font_name=font_name)
        return self

    def _paste(self, sub_img, pos, size, alpha, use_shadow, shadow_width, shadow_alpha):
        apos = self._abs(pos)
        w, h = (size if size else sub_img.size)
        shadow = image_shadow(alpha=shadow_alpha, offset=(0, 0), sigma=max(0.5, shadow_width / 2),
                              color=(0, 0, 0, 255)) if use_shadow else None
        self._b.image(self._mem_image(sub_img), apos, (w, h), fit="stretch",
                      alpha=1.0 if alpha is None else float(alpha), shadow=shadow)
        return self

    def paste(self, sub_img, pos, size=None, use_shadow=False, shadow_width=8, shadow_alpha=0.6,
              exclude_on_hash=False):
        return self._paste(sub_img, pos, size, None, use_shadow, shadow_width, shadow_alpha)

    def paste_with_alpha_blend(self, sub_img, pos, size=None, alpha=None, use_shadow=False,
                               shadow_width=8, shadow_alpha=0.6, exclude_on_hash=False):
        return self._paste(sub_img, pos, size, alpha, use_shadow, shadow_width, shadow_alpha)

    def rect(self, pos, size, fill, stroke=None, stroke_width=1, exclude_on_hash=False):
        apos = self._abs(pos)
        self._b.rect(apos, size, fill=self._fill(fill, apos, size),
                     stroke=None if stroke is None else _rgba(stroke), stroke_width=stroke_width)
        return self

    def roundrect(self, pos, size, fill, radius, stroke=None, stroke_width=1,
                  corners=(True, True, True, True), exclude_on_hash=False):
        apos = self._abs(pos)
        self._b.roundrect(apos, size, radius, fill=self._fill(fill, apos, size), corners=corners,
                          stroke=None if stroke is None else _rgba(stroke), stroke_width=stroke_width)
        return self

    def pieslice(self, pos, size, start_angle, end_angle, fill, stroke=None, stroke_width=1,
                 exclude_on_hash=False):
        apos = self._abs(pos)
        self._b.pieslice(apos, size, start_angle, end_angle, fill=self._fill(fill, apos, size),
                         stroke=None if stroke is None else _rgba(stroke), stroke_width=stroke_width)
        return self

    def blurglass_roundrect(self, pos, size, fill, radius, blur=4, shadow_width=6, shadow_alpha=0.3,
                            corners=(True, True, True, True), exclude_on_hash=False):
        apos = self._abs(pos)
        self._b.blurglass(apos, size, radius, fill=_rgba(fill), shadow_alpha=shadow_alpha)
        return self

    def draw_random_triangle_bg(self, time_color, main_hue, size_fixed_rate, exclude_on_hash=False):
        self._b.triangle_bg(hour=self._bg_hour, time_color=bool(time_color),
                            main_hue=float(main_hue) if main_hue is not None else 0.0,
                            size_fixed_rate=float(size_fixed_rate or 0.0))
        return self
