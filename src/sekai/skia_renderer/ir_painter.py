"""IRPainter — a ``Painter``-API-compatible shim that records Render IR instead of
rasterizing with Pillow.

The plot.py widget tree (and direct-Painter drawers) render by calling a small set of
``Painter`` primitives. Subclassing ``Painter`` and overriding those primitives lets the
SAME widget tree emit a declarative IR scene (built via :class:`IRBuilder`) that the Rust
``render_scene`` interpreter draws — without touching any drawer. The widgets resolve their
own layout to concrete coordinates before calling us, so we only translate each call.

Coordinates: widgets pass positions in the current region's local space; we flatten to
absolute by adding ``self.offset`` (the same offset ``Painter.add_operation`` would capture).

Pristine images loaded by ``get_img_from_path`` retain their asset provenance and are emitted
as relative paths, letting Rust reuse its process-wide image cache. Generated or modified
images are shipped as ``mem:<key>`` images (see the renderer's mem-image support).

Anything not yet expressible raises :class:`SkiaUnsupported`; callers catch it and fall back
to the Pillow path, so coverage can grow incrementally without breaking endpoints.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from src.sekai.base.painter import (
    AdaptiveTextColor,
    FontDesc,
    LinearGradient,
    Painter,
    RadialGradient,
)
from src.sekai.base.utils import EncodedImageRef, get_pristine_image_asset_path, resolve_image_source_sync
from src.sekai.skia_renderer.ir_builder import (
    IRBuilder,
    adaptive_color,
    clip_rrect,
    image_shadow,
    linear_gradient,
    radial_gradient,
)


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
        self._assets_base_dir = Path(assets_base_dir).resolve()
        self._b = IRBuilder(
            size[0],
            size[1],
            assets_base_dir=assets_base_dir,
            font_dir=font_dir,
            default_font=default_font,
            bold_font=bold_font,
            heavy_font=heavy_font,
            emoji_font=emoji_font,
            export_format=export_format,
            jpg_quality=jpg_quality,
        )
        self._default_name = default_font
        self._bold_name = bold_font
        self._heavy_name = heavy_font
        self._bg_hour = bg_hour
        self._mem_images: dict[str, bytes] = {}
        # id(img) -> (img, key). Holding the PIL image keeps its id() from being
        # recycled by the allocator mid-draw — without the strong reference a GC'd
        # temporary image could alias a later image and paste the wrong pixels.
        self._mem_by_id: dict[int, tuple[Any, str]] = {}
        # push_clip_roundrect/push_mask open an IR Group whose children are group-relative;
        # _abs subtracts the accumulated origin so widget coords stay untouched.
        self._group_origin: tuple[float, float] = (0.0, 0.0)
        self._group_origin_stack: list[tuple[str, tuple[float, float]]] = []

    # ---- output ----

    @property
    def builder(self) -> IRBuilder:
        """The scene builder, for callers that splice this tree into a larger scene
        (see ``skia_renderer.canvas.build_canvas_ir``). Check :meth:`assert_balanced` first."""
        return self._b

    @property
    def mem_images(self) -> dict[str, Any]:
        """Runtime images referenced as ``mem:<key>``; pass to ``native.render_scene``."""
        return self._mem_images

    def assert_balanced(self) -> None:
        if self._group_origin_stack:
            raise SkiaUnsupported("unbalanced push_clip_roundrect/push_mask")

    def build_scene(self) -> tuple[dict, dict[str, Any]]:
        self.assert_balanced()
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
        return (
            pos[0] + self.offset[0] - self._group_origin[0],
            pos[1] + self.offset[1] - self._group_origin[1],
        )

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
        """Capture a runtime PIL image once as raw RGBA and return its ``mem:<key>`` ref.

        Raw transport (``(w, h, rgba_bytes)``) skips PNG encode/decode — ~1.6x faster
        end-to-end than shipping PNG, since the pixels are already in hand.
        """
        entry = self._mem_by_id.get(id(img))
        if entry is not None and entry[0] is img:
            return f"mem:{entry[1]}"
        key = f"m{len(self._mem_images)}"
        rgba = img if img.mode == "RGBA" else img.convert("RGBA")
        self._mem_images[key] = (rgba.width, rgba.height, rgba.tobytes())
        self._mem_by_id[id(img)] = (img, key)
        return f"mem:{key}"

    def _image_ref(self, img: Any) -> str:
        if isinstance(img, EncodedImageRef):
            entry = self._mem_by_id.get(id(img))
            if entry is not None and entry[0] is img:
                return f"mem:{entry[1]}"
            key = f"m{len(self._mem_images)}"
            # Plain bytes → decoded Rust-side (MemImage::Encoded); no Python decode at all.
            self._mem_images[key] = img.data
            self._mem_by_id[id(img)] = (img, key)
            return f"mem:{key}"
        source = get_pristine_image_asset_path(img)
        if source is not None:
            try:
                relative = source.resolve(strict=True).relative_to(self._assets_base_dir)
            except (FileNotFoundError, ValueError):
                pass
            else:
                if relative.parts and all(part not in ("", ".", "..") for part in relative.parts):
                    return relative.as_posix()
        if not isinstance(img, Image.Image):
            # AssetImageRef outside the assets root or vanished on disk: decode
            # (placeholder on missing) so mem transport still renders something.
            img = resolve_image_source_sync(img)
        return self._mem_image(img)

    def _fill(self, fill, apos, size):
        """Map a Painter fill (Color / LinearGradient / RadialGradient) to an IR fill."""
        if isinstance(fill, LinearGradient):
            return linear_gradient(
                _rgba(fill.c1),
                _rgba(fill.c2),
                (apos[0] + fill.p1[0] * size[0], apos[1] + fill.p1[1] * size[1]),
                (apos[0] + fill.p2[0] * size[0], apos[1] + fill.p2[1] * size[1]),
                method=fill.method,
            )
        if isinstance(fill, RadialGradient):
            return radial_gradient(
                _rgba(fill.c1),
                _rgba(fill.c2),
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
            adaptive = adaptive_color(
                _rgba(fill.light), _rgba(fill.dark), fill.threshold, pixelwise=bool(getattr(fill, "pixelwise", False))
            )
            fillval: Any = (0, 0, 0, 255)
        elif isinstance(fill, (LinearGradient, RadialGradient)):
            # Gradient text: map the gradient endpoints (fractions of the glyph overlay) to
            # absolute coords over the text bbox, mirroring Painter's gradient-overlay path
            # (overlay = text_size + 10px, composited at pos). The Rust Text node renders the
            # gradient as a glyph-masked shader.
            fillval = self._gradient_text_fill(fill, text, role, size, font_name, apos)
        else:
            fillval = _rgba(fill)
        self._b.text(
            text,
            apos,
            role,
            size,
            align=align,
            baseline="cjk_top",
            fill=fillval,
            adaptive=adaptive,
            font_name=font_name,
        )
        return self

    def _gradient_text_fill(self, fill, text, role, size, font_name, apos):
        """Map a Painter gradient text fill to an absolute-coord IR gradient over the glyph
        overlay (text ink size + 10px padding, anchored at the draw position)."""
        pil_font = self._b._pil_font(role, size, font_name)
        x0, y0, x1, y1 = pil_font.getbbox(text)
        overlay_size = ((x1 - x0) + 10, (y1 - y0) + 10)
        return self._fill(fill, apos, overlay_size)

    def _paste(
        self, sub_img, pos, size, alpha, use_shadow, shadow_width, shadow_alpha, src_rect=None, blend="src_over"
    ):
        apos = self._abs(pos)
        if size:
            w, h = size
        elif src_rect is not None:
            w, h = src_rect[2] - src_rect[0], src_rect[3] - src_rect[1]
        else:
            w, h = sub_img.size
        shadow = (
            image_shadow(alpha=shadow_alpha, offset=(0, 0), sigma=max(0.5, shadow_width / 2), color=(0, 0, 0, 255))
            if use_shadow
            else None
        )
        self._b.image(
            self._image_ref(sub_img),
            apos,
            (w, h),
            fit="stretch",
            alpha=1.0 if alpha is None else float(alpha),
            shadow=shadow,
            source_rect=src_rect,
            blend=blend,
        )
        return self

    def paste(
        self,
        sub_img,
        pos,
        size=None,
        use_shadow=False,
        shadow_width=8,
        shadow_alpha=0.6,
        src_rect=None,
        exclude_on_hash=False,
    ):
        return self._paste(sub_img, pos, size, None, use_shadow, shadow_width, shadow_alpha, src_rect)

    def paste_with_alpha_blend(
        self,
        sub_img,
        pos,
        size=None,
        alpha=None,
        use_shadow=False,
        shadow_width=8,
        shadow_alpha=0.6,
        src_rect=None,
        exclude_on_hash=False,
    ):
        return self._paste(sub_img, pos, size, alpha, use_shadow, shadow_width, shadow_alpha, src_rect)

    def _push_group(self, kind: str, apos: tuple[float, float]) -> None:
        self._group_origin_stack.append((kind, self._group_origin))
        self._group_origin = (self._group_origin[0] + apos[0], self._group_origin[1] + apos[1])

    def _pop_group(self, kind: str) -> None:
        if not self._group_origin_stack:
            raise SkiaUnsupported(f"pop_{kind} without a matching push")
        opened, origin = self._group_origin_stack.pop()
        if opened != kind:
            raise SkiaUnsupported(f"pop_{kind} closing a {opened} group")
        self._b.pop_group()
        self._group_origin = origin

    def paste_src(self, sub_img, pos, size=None, src_rect=None, exclude_on_hash=False):
        # True Porter-Duff Src, same as the Pillow side: the drawn rect REPLACES the destination.
        # Src-over would only agree where the destination is empty, and a public Painter primitive
        # must not depend on the caller honouring a prose caveat to keep the two backends aligned.
        return self._paste(sub_img, pos, size, None, False, 0, 0, src_rect, blend="src")

    def push_clip_roundrect(self, pos, size, radius, corners=(True, True, True, True), exclude_on_hash=False):
        apos = self._abs(pos)
        self._b.push_group(apos, (float(size[0]), float(size[1])), clip=clip_rrect(radius, corners))
        self._push_group("clip", apos)
        return self

    def pop_clip(self, exclude_on_hash=False):
        self._pop_group("clip")
        return self

    def push_mask(self, mask, pos, size, exclude_on_hash=False):
        # Group{mask} = saveLayer + DstIn, i.e. the layer's alpha times the mask's — the same
        # arithmetic Painter._impl_pop_mask applies with ImageChops.multiply.
        apos = self._abs(pos)
        self._b.push_group(apos, (float(size[0]), float(size[1])), mask=self._image_ref(mask))
        self._push_group("mask", apos)
        return self

    def pop_mask(self, exclude_on_hash=False):
        self._pop_group("mask")
        return self

    def shadow_roundrect(self, pos, size, radius, shadow_width=6, shadow_alpha=0.3, exclude_on_hash=False):
        apos = self._abs(pos)
        self._b.shadow(
            apos,
            (float(size[0]), float(size[1])),
            float(radius),
            alpha=float(shadow_alpha),
            offset=(0, 0),
            sigma=max(0.5, shadow_width / 2),
        )
        return self

    def rect(self, pos, size, fill, stroke=None, stroke_width=1, exclude_on_hash=False):
        apos = self._abs(pos)
        self._b.rect(
            apos,
            size,
            fill=self._fill(fill, apos, size),
            stroke=None if stroke is None else _rgba(stroke),
            stroke_width=stroke_width,
        )
        return self

    def roundrect(
        self,
        pos,
        size,
        fill,
        radius,
        stroke=None,
        stroke_width=1,
        corners=(True, True, True, True),
        exclude_on_hash=False,
    ):
        apos = self._abs(pos)
        self._b.roundrect(
            apos,
            size,
            radius,
            fill=self._fill(fill, apos, size),
            corners=corners,
            stroke=None if stroke is None else _rgba(stroke),
            stroke_width=stroke_width,
        )
        return self

    def pieslice(self, pos, size, start_angle, end_angle, fill, stroke=None, stroke_width=1, exclude_on_hash=False):
        apos = self._abs(pos)
        self._b.pieslice(
            apos,
            size,
            start_angle,
            end_angle,
            fill=self._fill(fill, apos, size),
            stroke=None if stroke is None else _rgba(stroke),
            stroke_width=stroke_width,
        )
        return self

    def blurglass_roundrect(
        self,
        pos,
        size,
        fill,
        radius,
        blur=4,
        shadow_width=6,
        shadow_alpha=0.3,
        corners=(True, True, True, True),
        exclude_on_hash=False,
    ):
        apos = self._abs(pos)
        self._b.blurglass(
            apos,
            size,
            radius,
            fill=self._fill(fill, apos, size),
            shadow_alpha=shadow_alpha,
            blur=float(blur),
            shadow_width=float(shadow_width),
            corners=tuple(corners),
        )
        return self

    def draw_random_triangle_bg(self, time_color, main_hue, size_fixed_rate, exclude_on_hash=False):
        self._b.triangle_bg(
            hour=self._bg_hour,
            time_color=bool(time_color),
            main_hue=float(main_hue) if main_hue is not None else 0.0,
            size_fixed_rate=float(size_fixed_rate or 0.0),
        )
        return self
