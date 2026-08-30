import asyncio
from collections.abc import Callable
import contextvars
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import logging
from types import TracebackType
from typing import Literal, Self, TypedDict

from PIL import Image, ImageFont

from src.core.pillow_telemetry import PILLOW_TOUCH_IMAGE_DECODE, record_pillow_touch

from .painter import (
    ALIGN_MAP,
    ALIGN_TYPE,
    BLACK,
    DEFAULT_FONT,
    ITEM_SIZE_MODE_TYPE,
    SHADOW,
    TRANSPARENT,
    Color,
    FontDesc,
    ImageSampling,
    ImageTint,
    LinearGradient,
    Painter,
    get_font,
    get_font_desc,
    get_text_size,
    pillow_resample_for_image_sampling,
)
from .utils import AssetImageRef, ImageSource, resolve_image_source_sync, run_in_pool

DEBUG = False
CANVAS_SIZE_LIMIT = [4096, 4096]

DEFAULT_PADDING = 0
DEFAULT_MARGIN = 0
DEFAULT_SEP = 8
_INVALID_ALIGN_MESSAGE = "Invalid align"
_GRID_DIMENSION_MESSAGE = "Either row_count or col_count should be None"


def _open_image_copy(path: str) -> Image.Image:
    """Open image file safely and detach data from file descriptor."""
    record_pillow_touch(PILLOW_TOUCH_IMAGE_DECODE)
    with Image.open(path) as img:
        img.load()
        return img.copy()


# =========================== 背景 =========================== #


class WidgetBg:
    def __init__(self) -> None:
        """Initialize the stateless background protocol base."""

    def draw(self, p: Painter) -> None:
        raise NotImplementedError()


class FillBg(WidgetBg):
    def __init__(self, fill: Color, stroke: Color | None = None, stroke_width: int = 1) -> None:
        super().__init__()
        self.fill = fill
        self.stroke = stroke
        self.stroke_width = stroke_width

    def draw(self, p: Painter) -> None:
        p.rect((0, 0), p.size, self.fill, self.stroke, self.stroke_width)


class RoundRectBg(WidgetBg):
    def __init__(
        self,
        fill: Color,
        radius: int,
        stroke: Color | None = None,
        stroke_width: int = 1,
        corners: tuple[bool, bool, bool, bool] = (True, True, True, True),
        blur_glass: bool = False,
        blur_glass_kwargs: dict | None = None,
    ) -> None:
        super().__init__()
        self.fill = fill
        self.radius = radius
        self.stroke = stroke
        self.stroke_width = stroke_width
        self.corners = corners
        self.blur_glass = blur_glass
        self.blur_glass_kwargs = blur_glass_kwargs or {}

    def draw(self, p: Painter) -> None:
        if self.blur_glass:
            p.blurglass_roundrect(
                (0, 0), p.size, self.fill, self.radius, corners=self.corners, **self.blur_glass_kwargs
            )
        else:
            p.roundrect((0, 0), p.size, self.fill, self.radius, self.stroke, self.stroke_width, self.corners)


class ImageBg(WidgetBg):
    def __init__(
        self,
        img: str | ImageSource,
        align: ALIGN_TYPE = "c",
        mode: Literal["fit", "fill", "fixed", "repeat"] = "fit",
        blur: bool = False,
        fade: float = 0.1,
    ) -> None:
        super().__init__()
        if isinstance(img, str):
            self.img = _open_image_copy(img)
        else:
            self.img = img
        assert align in ALIGN_MAP
        self.align = align
        assert mode in ("fit", "fill", "fixed", "repeat")
        self.mode = mode
        self.blur = blur
        self.fade = fade

    def draw(self, p: Painter) -> None:
        p.image_bg(self.img, align=self.align, mode=self.mode, blur=self.blur, fade=self.fade)


class RandomTriangleBg(WidgetBg):
    def __init__(self, time_color: bool, main_hue: float | None = None, size_fixed_rate: float = 0.0) -> None:
        super().__init__()
        self.time_color = time_color
        self.main_hue = main_hue
        self.size_fixed_rate = size_fixed_rate

    def draw(self, p: Painter) -> None:
        p.draw_random_triangle_bg(self.time_color, self.main_hue, self.size_fixed_rate)


# =========================== 布局类型 =========================== #


class Widget:
    _thread_local: contextvars.ContextVar | None = contextvars.ContextVar("local", default=None)

    def __init__(self) -> None:
        self.parent: Widget | None = None

        self.content_h_align = "l"
        self.content_v_align = "t"
        self.v_margin = DEFAULT_MARGIN
        self.h_margin = DEFAULT_MARGIN
        self.v_padding = DEFAULT_PADDING
        self.h_padding = DEFAULT_PADDING
        self.w = None
        self.h = None
        self.bg = None
        self.omit_parent_bg = False
        self.offset = (0, 0)
        self.offset_x_anchor = "l"
        self.offset_y_anchor = "t"
        self.allow_draw_outside = False

        self._calc_w = None
        self._calc_h = None

        self.draw_funcs = []

        self.userdata = {}

        self.drawn = False

        if Widget.get_current_widget():
            Widget.get_current_widget().add_item(self)

    def get_content_align(self) -> str | None:
        for k, v in ALIGN_MAP.items():
            if v == (self.content_h_align, self.content_v_align):
                return k
        return None

    @classmethod
    def get_current_widget_stack(cls) -> list[Self] | None:
        local = cls._thread_local.get()
        if local is None:
            return None
        return local.w_stack

    @classmethod
    def get_current_widget(cls) -> Self | None:
        stk = cls.get_current_widget_stack()
        if stk is None:
            return None
        return stk[-1]

    def __enter__(self) -> Self:
        local = self._thread_local.get()
        if local is None:

            class _WidgetLocal:
                def __init__(self):
                    self.w_stack = []

            local = _WidgetLocal()
        local.w_stack.append(self)
        self._thread_local.set(local)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        local = self._thread_local.get()
        assert local is not None, "Local variable not found"
        assert local.w_stack[-1] == self, "Widget stack mismatch"
        local.w_stack.pop()
        if not local.w_stack:
            self._thread_local.set(None)

    def add_item(self, item: Self) -> None:
        raise NotImplementedError()

    # def add_item(self, item: 'Widget', index: int = None):
    #     item.set_parent(self)
    #     if index is None:
    #         self.items.append(item)
    #     else:
    #         self.items.insert(index, item)
    #     return self

    def set_parent(self, parent: Self | None) -> Self:
        self.parent = parent
        return self

    def set_content_align(self, align: ALIGN_TYPE) -> Self:
        if align not in ALIGN_MAP:
            raise ValueError(_INVALID_ALIGN_MESSAGE)
        self.content_h_align, self.content_v_align = ALIGN_MAP[align]
        return self

    def set_margin(self, margin: int | tuple[int, int]) -> Self:
        if isinstance(margin, int):
            self.v_margin = margin
            self.h_margin = margin
        else:
            self.h_margin = margin[0]
            self.v_margin = margin[1]
        return self

    def set_padding(self, padding: int | tuple[int, int]) -> Self:
        if isinstance(padding, int):
            self.v_padding = padding
            self.h_padding = padding
        else:
            self.h_padding = padding[0]
            self.v_padding = padding[1]
        return self

    def set_size(self, size: tuple[int | None, int | None]) -> Self:
        if not size:
            size = (None, None)
        self.w = size[0]
        self.h = size[1]
        return self

    def set_w(self, w: int) -> Self:
        self.w = w
        return self

    def set_h(self, h: int) -> Self:
        self.h = h
        return self

    def set_offset(self, offset: tuple[int, int]) -> Self:
        self.offset = offset
        return self

    def set_offset_anchor(self, anchor: ALIGN_TYPE) -> Self:
        if anchor not in ALIGN_MAP:
            raise ValueError("Invalid anchor")
        self.offset_x_anchor, self.offset_y_anchor = ALIGN_MAP[anchor]
        return self

    def set_bg(self, bg: WidgetBg) -> Self:
        self.bg = bg
        return self

    def set_omit_parent_bg(self, omit: bool) -> Self:
        self.omit_parent_bg = omit
        return self

    def set_allow_draw_outside(self, allow: bool):
        self.allow_draw_outside = allow
        return self

    def _get_content_size(self) -> tuple[int, int]:
        return 0, 0

    def _get_self_size(self) -> tuple[int, int]:
        if not all([self._calc_w, self._calc_h]):
            content_w, content_h = self._get_content_size()
            content_w_limit = self.w - self.h_padding * 2 if self.w is not None else content_w
            content_h_limit = self.h - self.v_padding * 2 if self.h is not None else content_h
            if content_w > content_w_limit or content_h > content_h_limit:
                if not self.allow_draw_outside:
                    raise ValueError(
                        f"Content size is too large with ({content_w}, {content_h}) > "
                        f"({content_w_limit}, {content_h_limit})"
                    )
                else:
                    content_w = min(content_w, content_w_limit)
                    content_h = min(content_h, content_h_limit)
            self._calc_w = content_w_limit + self.h_margin * 2 + self.h_padding * 2
            self._calc_h = content_h_limit + self.v_margin * 2 + self.v_padding * 2
        return int(self._calc_w), int(self._calc_h)

    def _get_content_pos(self) -> tuple[int, int]:
        w, h = self._get_self_size()
        w -= self.h_padding * 2 + self.h_margin * 2
        h -= self.v_padding * 2 + self.v_margin * 2
        cw, ch = self._get_content_size()
        cx, cy = None, None
        if self.content_h_align == "l":
            cx = 0
        elif self.content_h_align == "r":
            cx = w - cw
        elif self.content_h_align == "c":
            cx = (w - cw) // 2
        if self.content_v_align == "t":
            cy = 0
        elif self.content_v_align == "b":
            cy = h - ch
        elif self.content_v_align == "c":
            cy = (h - ch) // 2
        assert cx is not None, "cx must not be None"
        assert cy is not None, "cy must not be None"
        return cx, cy

    def _draw_self(self, p: Painter) -> None:
        if DEBUG:
            import hashlib

            digest = hashlib.sha256(f"{self.__class__.__name__}:{p.w}:{p.h}".encode()).digest()
            color = (digest[0] % 201, digest[1] % 201, digest[2] % 201, 255)
            p.rect((0, 0), (p.w, p.h), TRANSPARENT, stroke=color, stroke_width=2)
            s = f"{self.__class__.__name__}({p.w},{p.h})"
            s += f"self={self._get_self_size()}"
            s += f"content={self._get_content_size()}"
            p.text(s, (3, 3), font=get_font_desc(DEFAULT_FONT, 16), fill=color)
            logging.debug(f"Draw {self.__class__.__name__} at {p.offset} size={p.size}")

        if self.bg:
            self.bg.draw(p)

        for draw_func in self.draw_funcs:
            draw_func(self, p)

    def _draw_content(self, p: Painter) -> None:
        """Draw no intrinsic content; subclasses opt in by overriding this hook."""

    def add_draw_func(self, draw_func: Callable[[Self, Painter], None]) -> Self:
        self.draw_funcs.append(draw_func)
        return self

    def clear_draw_funcs(self) -> Self:
        self.draw_funcs.clear()
        return self

    def draw(self, p: Painter) -> None:
        assert not self.drawn, "Only support draw once for each widget"
        self.drawn = True

        assert p.size == self._get_self_size()

        if self.offset_x_anchor == "l":
            offset_x = self.offset[0]
        elif self.offset_x_anchor == "r":
            offset_x = self.offset[0] - p.w
        else:
            offset_x = self.offset[0] - p.w // 2
        if self.offset_y_anchor == "t":
            offset_y = self.offset[1]
        elif self.offset_y_anchor == "b":
            offset_y = self.offset[1] - p.h
        else:
            offset_y = self.offset[1] - p.h // 2

        p.move_region((offset_x, offset_y))
        p.shrink_region((self.h_margin, self.v_margin))
        self._draw_self(p)

        p.shrink_region((self.h_padding, self.v_padding))
        cx, cy = self._get_content_pos()
        p.move_region((cx, cy))
        self._draw_content(p)

        p.restore_region(4)


class Frame(Widget):
    def __init__(self, items: list[Widget] | None = None) -> None:
        super().__init__()
        self.items = items or []
        for item in self.items:
            item.set_parent(self)

    def add_item(self, item: Widget) -> Self:
        item.set_parent(self)
        self.items.append(item)
        return self

    def set_items(self, items: list[Widget]) -> Self:
        for item in self.items:
            item.set_parent(None)
        self.items = items
        for item in self.items:
            item.set_parent(self)
        return self

    def _get_content_size(self) -> tuple[int, int]:
        size = (0, 0)
        for item in self.items:
            w, h = item._get_self_size()
            size = (max(size[0], w), max(size[1], h))
        return size

    def _draw_content(self, p: Painter) -> None:
        cw, ch = self._get_content_size()
        for item in self.items:
            w, h = item._get_self_size()
            x, y = 0, 0
            if self.content_h_align == "l":
                x = 0
            elif self.content_h_align == "r":
                x = cw - w
            elif self.content_h_align == "c":
                x = (cw - w) // 2
            if self.content_v_align == "t":
                y = 0
            elif self.content_v_align == "b":
                y = ch - h
            elif self.content_v_align == "c":
                y = (ch - h) // 2
            p.move_region((x, y), (w, h))
            item.draw(p)
            p.restore_region()


class HSplit(Widget):
    def __init__(
        self,
        items: list[Widget] | None = None,
        ratios: list[float] | None = None,
        sep: int = DEFAULT_SEP,
        item_size_mode: ITEM_SIZE_MODE_TYPE = "fixed",
        item_align: ALIGN_TYPE = "c",
    ) -> None:
        super().__init__()
        self.items = items or []
        for item in self.items:
            item.set_parent(self)
        self.ratios = ratios
        self.sep = sep
        assert item_size_mode in ("expand", "fixed")
        self.item_size_mode = item_size_mode
        if item_align not in ALIGN_MAP:
            raise ValueError(_INVALID_ALIGN_MESSAGE)
        self.item_h_align, self.item_valign = ALIGN_MAP[item_align]
        self.item_bg = None

    def set_items(self, items: list[Widget]) -> Self:
        for item in self.items:
            item.set_parent(None)
        self.items = items
        for item in self.items:
            item.set_parent(self)
        return self

    def add_item(self, item: Widget) -> Self:
        item.set_parent(self)
        self.items.append(item)
        return self

    def set_item_align(self, align: ALIGN_TYPE) -> Self:
        if align not in ALIGN_MAP:
            raise ValueError(_INVALID_ALIGN_MESSAGE)
        self.item_h_align, self.item_valign = ALIGN_MAP[align]
        return self

    def set_sep(self, sep: int) -> Self:
        self.sep = sep
        return self

    def set_ratios(self, ratios: list[float]) -> Self:
        self.ratios = ratios
        return self

    def set_item_size_mode(self, mode: ITEM_SIZE_MODE_TYPE) -> Self:
        assert mode in ("expand", "fixed")
        self.item_size_mode = mode
        return self

    def set_item_bg(self, bg: WidgetBg) -> Self:
        self.item_bg = bg
        return self

    def _get_item_sizes(self) -> list[tuple[int, int]]:
        ratios = self.ratios if self.ratios else [item._get_self_size()[0] for item in self.items]
        if self.item_size_mode == "expand":
            assert self.w is not None, "Expand mode requires width"
            ratio_sum = sum(ratios)
            unit_w = (self.w - self.sep * (len(ratios) - 1) - self.h_padding * 2) / ratio_sum
        else:
            unit_w = 0
            for r, item in zip(ratios, self.items):
                iw, _ih = item._get_self_size()
                if r > 0:
                    unit_w = max(unit_w, iw / r)
        ret = []
        h = max([item._get_self_size()[1] for item in self.items])
        for r, item in zip(ratios, self.items):
            ret.append((int(unit_w * r), h))
        return ret

    def _get_content_size(self) -> tuple[int, int]:
        if not self.items:
            return 0, 0
        sizes = self._get_item_sizes()
        return sum(s[0] for s in sizes) + self.sep * (len(sizes) - 1), max(s[1] for s in sizes)

    def _draw_content(self, p: Painter) -> None:
        if not self.items:
            return
        sizes = self._get_item_sizes()
        cur_x = 0
        for item, (w, h) in zip(self.items, sizes):
            iw, ih = item._get_self_size()
            p.move_region((cur_x, 0), (w, h))
            x, y = 0, 0
            if self.item_bg and not item.omit_parent_bg:
                self.item_bg.draw(p)
            if self.item_h_align == "l":
                x += 0
            elif self.item_h_align == "r":
                x += w - iw
            elif self.item_h_align == "c":
                x += (w - iw) // 2
            if self.item_valign == "t":
                y += 0
            elif self.item_valign == "b":
                y += h - ih
            elif self.item_valign == "c":
                y += (h - ih) // 2
            p.move_region((x, y), (iw, ih))
            item.draw(p)
            p.restore_region(2)
            cur_x += w + self.sep


class VSplit(Widget):
    def __init__(
        self,
        items: list[Widget] | None = None,
        ratios: list[float] | None = None,
        sep: int = DEFAULT_SEP,
        item_size_mode: ITEM_SIZE_MODE_TYPE = "fixed",
        item_align: ALIGN_TYPE = "c",
    ) -> None:
        super().__init__()
        self.items = items or []
        for item in self.items:
            item.set_parent(self)
        self.ratios = ratios
        self.sep = sep
        assert item_size_mode in ("expand", "fixed")
        self.item_size_mode = item_size_mode
        if item_align not in ALIGN_MAP:
            raise ValueError(_INVALID_ALIGN_MESSAGE)
        self.item_h_align, self.item_valign = ALIGN_MAP[item_align]
        self.item_bg = None

    def set_items(self, items: list[Widget]) -> Self:
        for item in self.items:
            item.set_parent(None)
        self.items = items
        for item in self.items:
            item.set_parent(self)
        return self

    def add_item(self, item: Widget) -> Self:
        item.set_parent(self)
        self.items.append(item)
        return self

    def set_item_align(self, align: ALIGN_TYPE) -> Self:
        if align not in ALIGN_MAP:
            raise ValueError(_INVALID_ALIGN_MESSAGE)
        self.item_h_align, self.item_valign = ALIGN_MAP[align]
        return self

    def set_sep(self, sep: int) -> Self:
        self.sep = sep
        return self

    def set_ratios(self, ratios: list[float]) -> Self:
        self.ratios = ratios
        return self

    def set_item_size_mode(self, mode: ITEM_SIZE_MODE_TYPE) -> Self:
        assert mode in ("expand", "fixed")
        self.item_size_mode = mode
        return self

    def set_item_bg(self, bg: WidgetBg) -> Self:
        self.item_bg = bg
        return self

    def _get_item_sizes(self) -> list[tuple[int, int]]:
        ratios = self.ratios if self.ratios else [item._get_self_size()[1] for item in self.items]
        if self.item_size_mode == "expand":
            assert self.h is not None, "Expand mode requires height"
            ratio_sum = sum(ratios)
            unit_h = (self.h - self.sep * (len(ratios) - 1) - self.v_padding * 2) / ratio_sum
        else:
            unit_h = 0
            for r, item in zip(ratios, self.items):
                _iw, ih = item._get_self_size()
                if r > 0:
                    unit_h = max(unit_h, ih / r)
        ret = []
        w = max([item._get_self_size()[0] for item in self.items])
        for r, item in zip(ratios, self.items):
            ret.append((w, int(unit_h * r)))
        return ret

    def _get_content_size(self) -> tuple[int, int]:
        if not self.items:
            return 0, 0
        sizes = self._get_item_sizes()
        return max(s[0] for s in sizes), sum(s[1] for s in sizes) + self.sep * (len(sizes) - 1)

    def _draw_content(self, p: Painter) -> None:
        if not self.items:
            return
        sizes = self._get_item_sizes()
        cur_y = 0
        for item, (w, h) in zip(self.items, sizes):
            iw, ih = item._get_self_size()
            p.move_region((0, cur_y), (w, h))
            if self.item_bg and not item.omit_parent_bg:
                self.item_bg.draw(p)
            x, y = 0, 0
            if self.item_h_align == "l":
                x += 0
            elif self.item_h_align == "r":
                x += w - iw
            elif self.item_h_align == "c":
                x += (w - iw) // 2
            if self.item_valign == "t":
                y += 0
            elif self.item_valign == "b":
                y += h - ih
            elif self.item_valign == "c":
                y += (h - ih) // 2
            p.move_region((x, y), (iw, ih))
            item.draw(p)
            p.restore_region(2)
            cur_y += h + self.sep


class Grid(Widget):
    def __init__(
        self,
        items: list[Widget] | None = None,
        row_count: int | None = None,
        col_count: int | None = None,
        item_size_mode: ITEM_SIZE_MODE_TYPE = "fixed",
        item_align: ALIGN_TYPE = "c",
        h_sep: int = DEFAULT_SEP,
        v_sep: int = DEFAULT_SEP,
        vertical: bool = False,
    ) -> None:
        super().__init__()
        self.items = items or []
        for item in self.items:
            item.set_parent(self)
        self.row_count = row_count
        self.col_count = col_count
        assert not (self.row_count and self.col_count), _GRID_DIMENSION_MESSAGE
        assert item_size_mode in ("expand", "fixed")
        self.item_size_mode = item_size_mode
        self.h_sep = h_sep
        self.v_sep = v_sep
        if item_align not in ALIGN_MAP:
            raise ValueError(_INVALID_ALIGN_MESSAGE)
        self.item_h_align, self.item_valign = ALIGN_MAP[item_align]
        self.item_bg = None
        self.vertical = vertical

    def set_vertical(self, vertical: bool) -> Self:
        self.vertical = vertical
        return self

    def set_items(self, items: list[Widget]) -> Self:
        for item in self.items:
            item.set_parent(None)
        self.items = items
        for item in self.items:
            item.set_parent(self)
        return self

    def add_item(self, item: Widget) -> Self:
        item.set_parent(self)
        self.items.append(item)
        return self

    def set_item_align(self, align: ALIGN_TYPE) -> Self:
        if align not in ALIGN_MAP:
            raise ValueError(_INVALID_ALIGN_MESSAGE)
        self.item_h_align, self.item_valign = ALIGN_MAP[align]
        return self

    def set_sep(self, h_sep: int | None = None, v_sep: int | None = None) -> Self:
        if h_sep is not None:
            self.h_sep = h_sep
        if v_sep is not None:
            self.v_sep = v_sep
        return self

    def set_row_count(self, count: int) -> Self:
        self.row_count = count
        self.col_count = None
        return self

    def set_col_count(self, count: int) -> Self:
        self.col_count = count
        self.row_count = None
        return self

    def set_item_size_mode(self, mode: ITEM_SIZE_MODE_TYPE) -> Self:
        assert mode in ("expand", "fixed")
        self.item_size_mode = mode
        return self

    def set_item_bg(self, bg: WidgetBg) -> Self:
        self.item_bg = bg
        return self

    def _get_grid_rc_and_size(self) -> tuple[tuple[int, int], tuple[int, int]]:
        r, c = self.row_count, self.col_count
        assert (r and not c) or (c and not r), _GRID_DIMENSION_MESSAGE
        if not r:
            r = (len(self.items) + c - 1) // c
        if not c:
            c = (len(self.items) + r - 1) // r
        if self.item_size_mode == "expand":
            assert self.w is not None, "Expand mode requires width"
            assert self.h is not None, "Expand mode requires height"
            gw = (self.w - self.h_sep * (c - 1) - self.h_padding * 2) / c
            gh = (self.h - self.v_sep * (r - 1) - self.v_padding * 2) / r
        else:
            gw, gh = 0, 0
            for item in self.items:
                iw, ih = item._get_self_size()
                gw = max(gw, iw)
                gh = max(gh, ih)
        return (int(r), int(c)), (int(gw), int(gh))

    def _get_content_size(self) -> tuple[int, int]:
        (r, c), (gw, gh) = self._get_grid_rc_and_size()
        return int(c * gw + self.h_sep * (c - 1)), int(r * gh + self.v_sep * (r - 1))

    def _draw_content(self, p: Painter) -> None:
        (r, c), (gw, gh) = self._get_grid_rc_and_size()
        for idx, item in enumerate(self.items):
            if not self.vertical:
                i, j = idx // c, idx % c
            else:
                i, j = idx % r, idx // r
            x = j * (gw + self.h_sep)
            y = i * (gh + self.v_sep)
            p.move_region((x, y), (gw, gh))
            if self.item_bg and not item.omit_parent_bg:
                self.item_bg.draw(p)
            x, y = 0, 0
            iw, ih = item._get_self_size()
            if self.item_h_align == "l":
                x += 0
            elif self.item_h_align == "r":
                x += gw - iw
            elif self.item_h_align == "c":
                x += (gw - iw) // 2
            if self.item_valign == "t":
                y += 0
            elif self.item_valign == "b":
                y += gh - ih
            elif self.item_valign == "c":
                y += (gh - ih) // 2
            p.move_region((x, y), (iw, ih))
            item.draw(p)
            p.restore_region(2)


class Flow(Widget):
    def __init__(
        self,
        items: list[Widget] | None = None,
        row_count: int | None = None,
        col_count: int | None = None,
        width: int | None = None,
        height: int | None = None,
        aspect_ratio: float | None = None,
        item_align: ALIGN_TYPE = "lt",
        h_sep: int = DEFAULT_SEP,
        v_sep: int = DEFAULT_SEP,
        vertical: bool = False,
        keep_empty_row_or_col: bool = False,
    ):
        """
        Flow布局，逐行或逐列排列子组件，自动换行或换列。需要至少指定以下一个参数用于计算布局：
        - row_count: 总行数 (仅horizontal模式)
        - col_count: 总列数 (仅vertical模式)
        - width: 每行宽度限制 (仅horizontal模式)
        - height: 每列高度限制 (仅vertical模式)
        - aspect_ratio: 期望宽高比
        """
        super().__init__()
        self.items = items or []
        for item in self.items:
            item.set_parent(self)
        self.row_count = row_count
        self.col_count = col_count
        self.aspect_ratio = aspect_ratio
        self.h_sep = h_sep
        self.v_sep = v_sep
        self.vertical = vertical
        self.keep_empty_row_or_col = keep_empty_row_or_col
        self.set_item_align(item_align)

        self.layout: list[list[int]] = None
        self.total_size: tuple[int, int] = None
        self.item_positions: list[tuple[int, int]] = None

    def set_item_align(self, align: ALIGN_TYPE):
        if align not in ALIGN_MAP:
            raise ValueError(_INVALID_ALIGN_MESSAGE)
        self.item_halign, self.item_valign = ALIGN_MAP[align]
        return self

    def set_vertical(self, vertical: bool):
        self.vertical = vertical
        return self

    def set_sep(self, h_sep=None, v_sep=None):
        if h_sep is not None:
            self.h_sep = h_sep
        if v_sep is not None:
            self.v_sep = v_sep
        return self

    def set_row_or_col_count(self, row_count: int | None = None, col_count: int | None = None):
        assert not (row_count and col_count), _GRID_DIMENSION_MESSAGE
        self.row_count = row_count
        self.col_count = col_count
        return self

    def set_aspect_ratio(self, aspect_ratio: float):
        self.aspect_ratio = aspect_ratio
        return self

    def set_keep_empty_row_or_col(self, keep: bool):
        self.keep_empty_row_or_col = keep
        return self

    def add_item(self, item: Widget) -> Self:
        item.set_parent(self)
        self.items.append(item)
        return self

    def _horizontal_group_size(self, row: list[int]) -> tuple[int, int]:
        sizes = [self.items[index]._get_self_size() for index in row]
        width = sum(item_width for item_width, _ in sizes) + self.h_sep * max(0, len(row) - 1)
        height = max((item_height for _, item_height in sizes), default=0)
        return width, height

    def _vertical_group_size(self, col: list[int]) -> tuple[int, int]:
        sizes = [self.items[index]._get_self_size() for index in col]
        width = max((item_width for item_width, _ in sizes), default=0)
        height = sum(item_height for _, item_height in sizes) + self.v_sep * max(0, len(col) - 1)
        return width, height

    def _horizontal_layout_size(self, layout: list[list[int]]) -> tuple[int, int]:
        row_sizes = [self._horizontal_group_size(row) for row in layout]
        return self._horizontal_layout_size_from_groups(row_sizes)

    def _horizontal_layout_size_from_groups(self, row_sizes: list[tuple[int, int]]) -> tuple[int, int]:
        width = max((row_width for row_width, _ in row_sizes), default=0)
        height = sum(row_height for _, row_height in row_sizes) + self.v_sep * max(0, len(row_sizes) - 1)
        return width, height

    def _vertical_layout_size(self, layout: list[list[int]]) -> tuple[int, int]:
        col_sizes = [self._vertical_group_size(col) for col in layout]
        return self._vertical_layout_size_from_groups(col_sizes)

    def _vertical_layout_size_from_groups(self, col_sizes: list[tuple[int, int]]) -> tuple[int, int]:
        width = sum(col_width for col_width, _ in col_sizes) + self.h_sep * max(0, len(col_sizes) - 1)
        height = max((col_height for _, col_height in col_sizes), default=0)
        return width, height

    def _calc_total_size_by_layout_fast(self, layout: list[list[int]]) -> tuple[int, int]:
        if not layout:
            return (0, 0)
        if self.vertical:
            return self._vertical_layout_size(layout)
        return self._horizontal_layout_size(layout)

    @staticmethod
    def _alignment_offset(container_size: int, item_size: int, alignment: str) -> int:
        if alignment in {"l", "t"}:
            return 0
        if alignment in {"r", "b"}:
            return container_size - item_size
        return (container_size - item_size) // 2

    def _horizontal_item_positions(
        self,
        layout: list[list[int]],
        row_sizes: list[tuple[int, int]],
        total_width: int,
    ) -> list[tuple[int, int]]:
        item_positions = [(0, 0) for _ in self.items]
        row_y = 0
        for row, (row_width, row_height) in zip(layout, row_sizes, strict=True):
            item_x = self._alignment_offset(total_width, row_width, self.item_halign)
            for index in row:
                item_width, item_height = self.items[index]._get_self_size()
                item_y = row_y + self._alignment_offset(row_height, item_height, self.item_valign)
                item_positions[index] = (item_x, item_y)
                item_x += item_width + self.h_sep
            row_y += row_height + self.v_sep
        return item_positions

    def _vertical_item_positions(
        self,
        layout: list[list[int]],
        col_sizes: list[tuple[int, int]],
        total_height: int,
    ) -> list[tuple[int, int]]:
        item_positions = [(0, 0) for _ in self.items]
        col_x = 0
        for col, (col_width, col_height) in zip(layout, col_sizes, strict=True):
            item_y = self._alignment_offset(total_height, col_height, self.item_valign)
            for index in col:
                item_width, item_height = self.items[index]._get_self_size()
                item_x = col_x + self._alignment_offset(col_width, item_width, self.item_halign)
                item_positions[index] = (item_x, item_y)
                item_y += item_height + self.v_sep
            col_x += col_width + self.h_sep
        return item_positions

    def _horizontal_layout_geometry(self, layout: list[list[int]]) -> tuple[tuple[int, int], list[tuple[int, int]]]:
        row_sizes = [self._horizontal_group_size(row) for row in layout]
        total_size = self._horizontal_layout_size_from_groups(row_sizes)
        return total_size, self._horizontal_item_positions(layout, row_sizes, total_size[0])

    def _vertical_layout_geometry(self, layout: list[list[int]]) -> tuple[tuple[int, int], list[tuple[int, int]]]:
        col_sizes = [self._vertical_group_size(col) for col in layout]
        total_size = self._vertical_layout_size_from_groups(col_sizes)
        return total_size, self._vertical_item_positions(layout, col_sizes, total_size[1])

    def _calc_total_size_and_item_pos_by_layout(
        self, layout: list[list[int]]
    ) -> tuple[tuple[int, int], list[tuple[int, int]]]:
        if not layout:
            return (0, 0), []
        if self.vertical:
            return self._vertical_layout_geometry(layout)
        return self._horizontal_layout_geometry(layout)

    @staticmethod
    def _balanced_layout(item_sizes: list[int], group_count: int) -> list[list[int]]:
        layout = [[] for _ in range(group_count)]
        target_size = sum(item_sizes) // group_count
        current_index = 0
        for group in layout:
            group_size = 0
            while current_index < len(item_sizes):
                group.append(current_index)
                group_size += item_sizes[current_index]
                current_index += 1
                if group_size >= target_size:
                    break
        if layout:
            layout[-1].extend(range(current_index, len(item_sizes)))
        return layout

    def _row_count_layout(self, row_count: int, col_count: int | None, aspect_ratio: float | None) -> list[list[int]]:
        assert not self.vertical, "Row count only works in horizontal mode"
        assert not col_count, "Cannot specify both row_count and col_count"
        assert not aspect_ratio, "Cannot specify both row_count and aspect_ratio"
        item_widths = [item._get_self_size()[0] for item in self.items]
        return self._balanced_layout(item_widths, row_count)

    def _col_count_layout(self, row_count: int | None, col_count: int, aspect_ratio: float | None) -> list[list[int]]:
        assert self.vertical, "Column count only works in vertical mode"
        assert not row_count, "Cannot specify both row_count and col_count"
        assert not aspect_ratio, "Cannot specify both col_count and aspect_ratio"
        item_heights = [item._get_self_size()[1] for item in self.items]
        return self._balanced_layout(item_heights, col_count)

    def _aspect_ratio_layout(self, aspect_ratio: float) -> list[list[int]]:
        candidates = (
            (self._calc_item_layout(col_count=count) if self.vertical else self._calc_item_layout(row_count=count))
            for count in range(1, len(self.items) + 1)
        )
        return min(candidates, key=lambda layout: abs(self._layout_aspect_ratio(layout) - aspect_ratio))

    def _layout_aspect_ratio(self, layout: list[list[int]]) -> float:
        width, height = self._calc_total_size_by_layout_fast(layout)
        return width / height if height > 0 else 1.0

    def _wrapped_layout(self, *, vertical: bool) -> list[list[int]]:
        limit = self.h if vertical else self.w
        padding = self.v_padding if vertical else self.h_padding
        separator = self.v_sep if vertical else self.h_sep
        size_index = 1 if vertical else 0
        assert limit is not None

        layout: list[list[int]] = []
        current_group: list[int] = []
        current_size = 0
        for index, item in enumerate(self.items):
            item_size = item._get_self_size()[size_index]
            projected_size = current_size + item_size + len(current_group) * separator + padding * 2
            if current_group and projected_size > limit:
                layout.append(current_group)
                current_group = []
                current_size = 0
            current_group.append(index)
            current_size += item_size
        if current_group:
            layout.append(current_group)
        return layout

    def _calc_item_layout(
        self, row_count: int | None = None, col_count: int | None = None, aspect_ratio: float | None = None
    ) -> list[list[int]]:
        if not self.items:
            if row_count or col_count:
                layout = [[] for _ in range(row_count or col_count)]
            else:
                layout = []
        elif row_count:
            layout = self._row_count_layout(row_count, col_count, aspect_ratio)
        elif col_count:
            layout = self._col_count_layout(row_count, col_count, aspect_ratio)
        elif aspect_ratio:
            layout = self._aspect_ratio_layout(aspect_ratio)
        elif not self.vertical and self.w:
            layout = self._wrapped_layout(vertical=False)
        elif self.vertical and self.h:
            layout = self._wrapped_layout(vertical=True)
        else:
            raise ValueError(
                "Either row_count, col_count, aspect_ratio, width (for horizontal) or height (for vertical)"
                " must be specified to calculate flow layout"
            )
        if not self.keep_empty_row_or_col:
            layout = [row for row in layout if row]
        return layout

    def _get_total_size_and_item_pos(self) -> tuple[tuple[int, int], list[tuple[int, int]]]:
        if self.layout is None or self.total_size is None or self.item_positions is None:
            layout = self._calc_item_layout(self.row_count, self.col_count, self.aspect_ratio)
            total_size, item_pos = self._calc_total_size_and_item_pos_by_layout(layout)
            self.layout = layout
            self.total_size = total_size
            self.item_positions = item_pos
        return self.total_size, self.item_positions

    def _get_content_size(self):
        total_size, _ = self._get_total_size_and_item_pos()
        return total_size

    def _draw_content(self, p: Painter):
        _, item_pos = self._get_total_size_and_item_pos()
        for idx, item in enumerate(self.items):
            x, y = item_pos[idx]
            iw, ih = item._get_self_size()
            p.move_region((x, y), (iw, ih))
            item.draw(p)
            p.restore_region()


@dataclass
class TextStyle:
    font: str = DEFAULT_FONT
    size: int = 16
    color: tuple[int, int, int] | tuple[int, int, int, int] = BLACK
    use_shadow: bool = False
    shadow_offset: tuple[int, int] | int = 1
    shadow_color: tuple[int, int, int, int] = SHADOW

    def replace(
        self,
        font: str | None = None,
        size: int | None = None,
        color: tuple[int, int, int, int] | None = None,
        use_shadow: bool | None = None,
        shadow_offset: tuple[int, int] | int | None = None,
        shadow_color: tuple[int, int, int, int] | None = None,
    ):
        return TextStyle(
            font=font if font is not None else self.font,
            size=size if size is not None else self.size,
            color=color if color is not None else self.color,
            use_shadow=use_shadow if use_shadow is not None else self.use_shadow,
            shadow_offset=shadow_offset if shadow_offset is not None else self.shadow_offset,
            shadow_color=shadow_color if shadow_color is not None else self.shadow_color,
        )


class TextBox(Widget):
    r"""TextBox

    绘制文字
    """

    def __init__(
        self,
        text: str = "",
        style: TextStyle = None,
        line_count: int | None = None,
        line_sep: int = 2,
        wrap: bool = True,
        overflow: Literal["shrink", "clip"] = "clip",
        use_real_line_count: bool = False,
    ) -> None:
        """
        overflow: 'shrink', 'clip'
        """
        super().__init__()
        self.text = str(text)
        self.style = style or TextStyle()
        self.line_count = line_count
        self.line_sep = line_sep
        self.wrap = wrap
        assert overflow in ("shrink", "clip")
        self.overflow = overflow
        self.use_real_line_count = use_real_line_count
        self.text_offset_x = 0
        self.text_offset_y = 0

        if line_count is None:
            self.line_count = 99999 if use_real_line_count else 1

        self.set_padding(2)
        self.set_margin(0)

    def set_text(self, text: str) -> Self:
        self.text = text
        return self

    def set_style(self, style: TextStyle) -> Self:
        self.style = style
        return self

    def set_line_count(self, count: int) -> Self:
        self.line_count = count
        return self

    def set_line_sep(self, sep: int) -> Self:
        self.line_sep = sep
        return self

    def set_wrap(self, wrap: bool) -> Self:
        self.wrap = wrap
        return self

    def set_overflow(self, overflow: str) -> None:
        assert overflow in ("shrink", "clip")
        self.overflow = overflow

    def set_text_offset(self, offset: tuple[int, int]):
        self.text_offset_x = offset[0]
        self.text_offset_y = offset[1]
        return self

    def _get_pil_font(self) -> ImageFont:
        return get_font(self.style.font, self.style.size)

    def _get_font_desc(self) -> FontDesc:
        return get_font_desc(self.style.font, self.style.size)

    def _get_clip_text_to_width_idx(self, text: str, width: int, suffix: str = "") -> tuple[int, int] | None:
        font = self._get_pil_font()
        w, _ = get_text_size(font, text + suffix)
        if w <= width:
            return None
        left_idx, right_idx = 0, len(text)
        while left_idx <= right_idx:
            mid_idx = (left_idx + right_idx) // 2
            w, _ = get_text_size(font, text[:mid_idx] + suffix)
            if w < width:
                left_idx = mid_idx + 1
            elif w > width:
                right_idx = mid_idx - 1
            else:
                return mid_idx
        return right_idx

    def _wrap_line_to_width(self, line: str, width: int, suffix: str, preceding_lines: int) -> list[str]:
        wrapped_lines: list[str] = []
        while True:
            line_suffix = suffix if preceding_lines + len(wrapped_lines) == self.line_count - 1 else ""
            clip_idx = self._get_clip_text_to_width_idx(line, width, line_suffix)
            if clip_idx is None:
                wrapped_lines.append(line)
                break
            split_at = 1 if clip_idx == 0 else clip_idx
            wrapped_lines.append(line[:split_at] + line_suffix)
            line = line[split_at:]
            if preceding_lines + len(wrapped_lines) >= self.line_count:
                break
        return wrapped_lines

    def _clip_line_to_width(self, line: str, width: int, suffix: str) -> str:
        clip_idx = self._get_clip_text_to_width_idx(line, width, suffix)
        if clip_idx is None:
            return line
        return line[:clip_idx] + suffix

    def _get_lines(self) -> list[str]:
        if not self.w:
            return self.text.split("\n")[: self.line_count]

        width = self.w - self.h_padding * 2
        suffix = "..." if self.overflow == "shrink" else ""
        clipped_lines: list[str] = []
        for line in self.text.split("\n"):
            if self.wrap:
                clipped_lines.extend(self._wrap_line_to_width(line, width, suffix, len(clipped_lines)))
            else:
                clipped_lines.append(self._clip_line_to_width(line, width, suffix))
        return clipped_lines[: self.line_count]

    def _get_content_size(self) -> tuple[int, int]:
        lines = self._get_lines()
        w, h = 0, 0
        font = self._get_pil_font()
        for line in lines:
            lw, _ = get_text_size(font, line)
            w = max(w, lw)
        line_count = len(lines) if self.use_real_line_count else self.line_count
        h = line_count * (self.style.size + self.line_sep) - self.line_sep
        if self.w:
            w = self.w - self.h_padding * 2
        if self.h:
            h = self.h - self.v_padding * 2
        return w, h

    def _draw_content(self, p: Painter) -> None:
        font = self._get_pil_font()
        lines = self._get_lines()
        text_h = (self.style.size + self.line_sep) * len(lines) - self.line_sep
        start_y = None
        if self.content_v_align == "t":
            start_y = 0
        elif self.content_v_align == "b":
            start_y = p.h - text_h
        elif self.content_v_align == "c":
            start_y = (p.h - text_h) // 2
        assert start_y is not None
        for i, line in enumerate(lines):
            lw, _ = get_text_size(font, line)
            x, y = 0, start_y + i * (self.style.size + self.line_sep)
            if self.content_h_align == "l":
                x += 0
            elif self.content_h_align == "r":
                x += p.w - lw
            elif self.content_h_align == "c":
                x += (p.w - lw) // 2
            x += self.text_offset_x
            y += self.text_offset_y
            p.move_region((x, y), (lw, self.style.size))
            if self.style.use_shadow:
                shadow_offset = self.style.shadow_offset
                if isinstance(shadow_offset, int):
                    shadow_offset = (shadow_offset, shadow_offset)
                p.text(line, shadow_offset, font=self._get_font_desc(), fill=self.style.shadow_color)
            p.text(line, (0, 0), font=self._get_font_desc(), fill=self.style.color)
            p.restore_region()


class Seg(TypedDict):
    text: str
    color: tuple[int, int, int] | None


class ColoredTextBox(Widget):
    def __init__(
        self,
        text: str = "",
        style: TextStyle = None,
        line_count: int | None = None,
        line_sep: int = 2,
        wrap: bool = True,
        overflow: Literal["shrink", "clip"] = "clip",
        use_real_line_count: bool = False,
    ) -> None:
        super().__init__()
        self.text = str(text)
        self.style = style or TextStyle()
        self.line_count = line_count
        self.line_sep = line_sep
        self.wrap = wrap
        assert overflow in ("shrink", "clip")
        self.overflow = overflow
        self.use_real_line_count = use_real_line_count
        self.text_offset_x = 0
        self.text_offset_y = 0

        if line_count is None:
            self.line_count = 99999 if use_real_line_count else 1

        self.set_padding(2)
        self.set_margin(0)

    def set_text(self, text: str) -> Self:
        self.text = text
        return self

    def set_style(self, style: TextStyle) -> Self:
        self.style = style
        return self

    def set_line_count(self, count: int) -> Self:
        self.line_count = count
        return self

    def set_line_sep(self, sep: int) -> Self:
        self.line_sep = sep
        return self

    def set_wrap(self, wrap: bool) -> Self:
        self.wrap = wrap
        return self

    def set_overflow(self, overflow: str) -> Self:
        assert overflow in ("shrink", "clip")
        self.overflow = overflow
        return self

    def set_text_offset(self, offset: tuple[int, int]) -> Self:
        self.text_offset_x = offset[0]
        self.text_offset_y = offset[1]
        return self

    def _get_pil_font(self) -> ImageFont:
        return get_font(self.style.font, self.style.size)

    def _get_font_desc(self) -> FontDesc:
        return get_font_desc(self.style.font, self.style.size)

    def _get_render_color(self, color: tuple[int, int, int] | None):
        if color is None:
            return self.style.color
        alpha = self.style.color[3] if len(self.style.color) == 4 else 255
        return (*color, alpha)

    def _get_line_width(self, font: ImageFont, line: list[Seg]) -> int:
        return sum(get_text_size(font, seg["text"])[0] for seg in line if seg["text"])

    def _append_colored_text(self, line: list[Seg], text: str, color: tuple[int, int, int] | None) -> None:
        if not text:
            return
        if line and line[-1]["color"] == color:
            line[-1]["text"] += text
        else:
            line.append({"text": text, "color": color})

    def _shrink_line_with_suffix(self, font: ImageFont, line: list[Seg], width: int, suffix: str = "...") -> None:
        if width <= 0:
            line.clear()
            return

        suffix_width, _ = get_text_size(font, suffix)
        while line and self._get_line_width(font, line) + suffix_width > width:
            line[-1]["text"] = line[-1]["text"][:-1]
            if not line[-1]["text"]:
                line.pop()

        if not line and suffix_width > width:
            return

        suffix_color = line[-1]["color"] if line else None
        self._append_colored_text(line, suffix, suffix_color)

    def _iter_colored_characters(self):
        for seg in parse_colored_text_segments(self.text):
            for character in seg["text"]:
                yield character, seg["color"]

    def _append_colored_character(
        self,
        lines: list[list[Seg]],
        character: str,
        color: tuple[int, int, int] | None,
        character_width: int,
        current_width: int,
        max_width: int | None,
    ) -> tuple[int, bool]:
        if character == "\n":
            if len(lines) >= self.line_count:
                return current_width, True
            lines.append([])
            return 0, False

        if max_width is not None and current_width + character_width > max_width and lines[-1]:
            if not self.wrap or len(lines) >= self.line_count:
                return current_width, True
            lines.append([])
            current_width = 0

        self._append_colored_text(lines[-1], character, color)
        return current_width + character_width, False

    def _get_lines(self) -> list[list[Seg]]:
        font = self._get_pil_font()
        max_width = self.w - self.h_padding * 2 if self.w else None
        lines: list[list[Seg]] = [[]]
        current_width = 0
        truncated = False

        for character, color in self._iter_colored_characters():
            character_width, _ = get_text_size(font, character)
            current_width, truncated = self._append_colored_character(
                lines, character, color, character_width, current_width, max_width
            )
            if truncated:
                break

        lines = lines[: self.line_count]
        if truncated and self.overflow == "shrink" and max_width is not None and lines:
            self._shrink_line_with_suffix(font, lines[-1], max_width)
        return lines

    def _get_content_size(self) -> tuple[int, int]:
        lines = self._get_lines()
        w, h = 0, 0
        font = self._get_pil_font()
        for line in lines:
            w = max(w, self._get_line_width(font, line))
        line_count = len(lines) if self.use_real_line_count else self.line_count
        h = line_count * (self.style.size + self.line_sep) - self.line_sep
        if self.w:
            w = self.w - self.h_padding * 2
        if self.h:
            h = self.h - self.v_padding * 2
        return w, h

    def _draw_content(self, p: Painter) -> None:
        font = self._get_pil_font()
        lines = self._get_lines()
        text_h = (self.style.size + self.line_sep) * len(lines) - self.line_sep
        start_y = None
        if self.content_v_align == "t":
            start_y = 0
        elif self.content_v_align == "b":
            start_y = p.h - text_h
        elif self.content_v_align == "c":
            start_y = (p.h - text_h) // 2
        assert start_y is not None

        for i, line in enumerate(lines):
            line_width = self._get_line_width(font, line)
            x = 0
            if self.content_h_align == "r":
                x += p.w - line_width
            elif self.content_h_align == "c":
                x += (p.w - line_width) // 2

            y = start_y + i * (self.style.size + self.line_sep)
            for seg in line:
                text = seg["text"]
                if not text:
                    continue
                seg_width, _ = get_text_size(font, text)
                p.move_region((x, y), (seg_width, self.style.size))
                p.text(text, (0, 0), font=self._get_font_desc(), fill=self._get_render_color(seg["color"]))
                p.restore_region()
                x += seg_width


class ImageBox(Widget):
    def __init__(
        self,
        image: str | ImageSource,
        image_size_mode=None,
        size=None,
        use_alpha_blend=False,
        alpha_adjust=1.0,
        shadow=False,
        shadow_width=6,
        shadow_alpha=0.6,
        source_rect: tuple[float, float, float, float] | None = None,
        sampling: ImageSampling | None = None,
        tint: ImageTint | None = None,
    ) -> None:
        """
        image_size_mode: 'fit', 'fill', 'original'

        ``image`` may be a decoded PIL image, an absolute file path (decoded eagerly),
        or a lazy AssetImageRef/EncodedImageRef: the Skia path then passes the asset
        path / encoded bytes straight through and the Pillow fallback decodes on
        demand inside Painter (layout only needs ``.size``, which refs provide).
        """
        super().__init__()
        self.image_size_mode = None
        self.use_alpha_blend = None
        self.alpha_adjust = None
        self.source_rect = source_rect
        self.sampling = sampling
        self.tint = tint
        if isinstance(image, str):
            self.image = _open_image_copy(image)
        else:
            self.image = image

        if size:
            self.set_size(size)

        if image_size_mode is None:
            if size and (size[0] or size[1]):
                self.set_image_size_mode("fit")
            else:
                self.set_image_size_mode("original")
        else:
            self.set_image_size_mode(image_size_mode)

        self.set_margin(0)
        self.set_padding(0)

        self.set_use_alpha_blend(use_alpha_blend)
        self.set_alpha_adjust(alpha_adjust)
        self.set_shadow(shadow, shadow_width, shadow_alpha)

    def set_alpha_adjust(self, alpha_adjust: float) -> Self:
        self.alpha_adjust = alpha_adjust
        return self

    def set_use_alpha_blend(self, use_alpha_blend) -> Self:
        self.use_alpha_blend = use_alpha_blend
        return self

    def set_shadow(self, shadow: bool, shadow_width=6, shadow_alpha=0.3):
        self.shadow = shadow
        self.shadow_width = shadow_width
        self.shadow_alpha = shadow_alpha
        return self

    def set_image(self, image: str | ImageSource) -> Self:
        if isinstance(image, str):
            self.image = _open_image_copy(image)
        else:
            self.image = image
        return self

    def set_image_size_mode(self, mode: str) -> Self:
        assert mode in ("fit", "fill", "original")
        self.image_size_mode = mode
        return self

    def _source_size(self) -> tuple[float, float]:
        if self.source_rect is None:
            return self.image.size
        return self.source_rect[2] - self.source_rect[0], self.source_rect[3] - self.source_rect[1]

    def _target_bounds(self) -> tuple[float, float]:
        target_width = self.w - self.h_padding * 2 if self.w else 1_000_000
        target_height = self.h - self.v_padding * 2 if self.h else 1_000_000
        return target_width, target_height

    @staticmethod
    def _scaled_size(
        source_size: tuple[float, float], target_bounds: tuple[float, float], *, fit: bool
    ) -> tuple[int, int]:
        width, height = source_size
        target_width, target_height = target_bounds
        scale = (
            min(target_width / width, target_height / height)
            if fit
            else max(target_width / width, target_height / height)
        )
        return int(width * scale), int(height * scale)

    def _get_content_size(self) -> tuple[int, int] | None:
        source_size = self._source_size()
        if self.image_size_mode == "original":
            return int(source_size[0]), int(source_size[1])
        if self.image_size_mode == "fit":
            assert self.w is not None or self.h is not None, "Fit mode requires width or height"
            return self._scaled_size(source_size, self._target_bounds(), fit=True)
        if self.image_size_mode == "fill":
            assert self.w is not None or self.h is not None, "Fill mode requires width or height"
            if self.w and self.h:
                return int(self.w - self.h_padding * 2), int(self.h - self.v_padding * 2)
            return self._scaled_size(source_size, self._target_bounds(), fit=False)
        return None

    def _draw_content(self, p: Painter):
        w, h = self._get_content_size()
        if self.use_alpha_blend:
            p.paste_with_alpha_blend(
                self.image,
                (0, 0),
                (w, h),
                self.alpha_adjust,
                use_shadow=self.shadow,
                shadow_width=self.shadow_width,
                shadow_alpha=self.shadow_alpha,
                src_rect=self.source_rect,
                sampling=self.sampling,
                tint=self.tint,
            )
        else:
            p.paste(
                self.image,
                (0, 0),
                (w, h),
                use_shadow=self.shadow,
                shadow_width=self.shadow_width,
                shadow_alpha=self.shadow_alpha,
                src_rect=self.source_rect,
                sampling=self.sampling,
                tint=self.tint,
            )


class CanvasImageBox(Widget):
    """Place a nested :class:`Canvas` as one isolated image-like widget.

    Both backends consume the nested widget tree. Pillow rasterizes it lazily from
    :meth:`Painter.paste_canvas`; IRPainter lowers it to a native RasterSubscene, keeping
    Porter-Duff operations local and applying the final resize/shadow to the completed badge.
    """

    def __init__(
        self,
        canvas: "Canvas",
        image_size_mode: Literal["fit", "fill", "original"] | None = None,
        size: tuple[int | None, int | None] | None = None,
        *,
        shadow: bool = False,
        shadow_width: int = 6,
        shadow_alpha: float = 0.6,
        sampling: ImageSampling | None = None,
        cache_key: str | None = None,
        require_asset_backed: bool = False,
        skip_on_error: bool = False,
    ) -> None:
        super().__init__()
        self.canvas = canvas
        self.image_size_mode = image_size_mode or ("fit" if size and (size[0] or size[1]) else "original")
        if self.image_size_mode not in {"fit", "fill", "original"}:
            raise ValueError(f"unsupported canvas image size mode: {self.image_size_mode!r}")
        self.shadow = bool(shadow)
        self.shadow_width = int(shadow_width)
        self.shadow_alpha = float(shadow_alpha)
        self.sampling = sampling
        self.cache_key = cache_key
        self.require_asset_backed = bool(require_asset_backed)
        self.skip_on_error = bool(skip_on_error)
        if size is not None:
            self.set_size(size)
        self.set_margin(0)
        self.set_padding(0)

    @property
    def natural_size(self) -> tuple[int, int]:
        return self.canvas._get_self_size()

    def _get_content_size(self) -> tuple[int, int]:
        width, height = self.natural_size
        if self.image_size_mode == "original":
            return width, height

        assert self.w is not None or self.h is not None, f"{self.image_size_mode} mode requires width or height"
        if self.image_size_mode == "fill" and self.w is not None and self.h is not None:
            return self.w - self.h_padding * 2, self.h - self.v_padding * 2
        target_width = self.w - self.h_padding * 2 if self.w else 1_000_000
        target_height = self.h - self.v_padding * 2 if self.h else 1_000_000
        scale = (
            min(target_width / width, target_height / height)
            if self.image_size_mode == "fit"
            else max(target_width / width, target_height / height)
        )
        return int(width * scale), int(height * scale)

    def _draw_content(self, p: Painter) -> None:
        p.paste_canvas(
            self.canvas,
            (0, 0),
            self._get_content_size(),
            use_shadow=self.shadow,
            shadow_width=self.shadow_width,
            shadow_alpha=self.shadow_alpha,
            sampling=self.sampling,
            cache_key=self.cache_key,
            require_asset_backed=self.require_asset_backed,
            skip_on_error=self.skip_on_error,
        )


class Spacer(Widget):
    def __init__(self, w: int = 1, h: int = 1) -> None:
        super().__init__()
        self.set_size((w, h))

    def _get_content_size(self) -> tuple[int, int]:
        return self.w - 2 * self.h_padding, self.h - 2 * self.v_padding

    def _draw_content(self, p: Painter) -> None:
        """Remain empty; a spacer contributes only size and optional background."""


def _image_box_size_hint(widget) -> tuple[int, int] | None:
    """The exact size an ImageBox will paste its ref at, if computable pre-layout.

    ImageBox._get_content_size only needs the ref's header dimensions plus the
    widget's own w/h, so prefetch can warm the (small) resize-cache entry the draw
    will hit instead of parking a full-size decode that may be evicted long before
    the serial replay reads it (hundreds of list jackets exceed the byte budget)."""
    if not isinstance(widget, ImageBox):
        return None
    if widget.source_rect is not None:
        # A cropped paste must decode/crop before resizing; the whole-asset target-size cache
        # entry would be the wrong pixels and cannot warm this operation.
        return None
    try:
        w, h = widget._get_content_size()
    except Exception:
        return None
    if not w or not h or w <= 0 or h <= 0:
        return None
    if (w, h) == tuple(widget.image.size):
        return None
    return (int(w), int(h))


def _register_asset_ref(
    out: dict,
    seen_ids: set[int],
    image: AssetImageRef,
    hint: tuple[int, int] | None = None,
    resample: Image.Resampling | int = 0,
) -> None:
    out[(id(image), hint, int(resample))] = (image, hint, resample)
    seen_ids.add(id(image))


def _register_widget_image_ref(widget, out: dict, seen_ids: set[int]) -> None:
    image = getattr(widget, "image", None)
    if not isinstance(image, AssetImageRef):
        return
    hint = _image_box_size_hint(widget)
    resample = pillow_resample_for_image_sampling(widget.sampling) if hint is not None else 0
    _register_asset_ref(out, seen_ids, image, hint, resample)


def _register_widget_background_refs(widget, out: dict, seen_ids: set[int]) -> None:
    for holder in (getattr(widget, "bg", None), getattr(widget, "item_bg", None)):
        image = getattr(holder, "img", None)
        if isinstance(image, AssetImageRef):
            _register_asset_ref(out, seen_ids, image)


def _register_widget_prefetch_refs(widget, out: dict, seen_ids: set[int]) -> None:
    for image in getattr(widget, "prefetch_image_sources", None) or ():
        if isinstance(image, AssetImageRef) and id(image) not in seen_ids:
            _register_asset_ref(out, seen_ids, image)


def _collect_asset_refs(widget, out: dict, seen_ids: set[int] | None = None) -> None:
    """Gather lazy AssetImageRefs held by a widget tree (ImageBox images, bg images,
    and any widget-declared ``prefetch_image_sources`` extras) with target-size and
    resampling hints. ``seen_ids`` mirrors ``{key[0] for key in out}`` across the
    recursion so the extras dedupe stays O(1) per extra (a linear scan of ``out``
    goes quadratic on a several-hundred-card box tree)."""
    if seen_ids is None:
        seen_ids = {key[0] for key in out}
    _register_widget_image_ref(widget, out, seen_ids)
    _register_widget_background_refs(widget, out, seen_ids)
    # A widget may list its own ``image`` among the extras (CardFullThumbnailBox does —
    # ``layers.base`` is both). Do not add an unhinted full decode when that exact object
    # already has a display-size hint, but retain genuinely distinct size/sampling uses.
    _register_widget_prefetch_refs(widget, out, seen_ids)
    for child in getattr(widget, "items", None) or ():
        _collect_asset_refs(child, out, seen_ids)


async def prefetch_asset_refs(root: Widget) -> None:
    """Decode every lazy AssetImageRef in the tree into the global caches
    concurrently — at the display size when known — so the serial Pillow draw hits
    warm caches instead of decoding inline op by op. No-op for trees without refs
    (the Skia path never calls this)."""
    refs: dict[tuple, tuple[AssetImageRef, tuple[int, int] | None, Image.Resampling | int]] = {}
    _collect_asset_refs(root, refs)
    if not refs:
        return
    unique: dict[tuple, tuple[AssetImageRef, tuple[int, int] | None, Image.Resampling | int]] = {}
    for ref, hint, resample in refs.values():
        unique.setdefault((str(ref.path), ref.mtime_ns, ref.file_size, hint, int(resample)), (ref, hint, resample))
    await asyncio.gather(
        *(run_in_pool(resolve_image_source_sync, ref, hint, resample) for ref, hint, resample in unique.values())
    )


class Canvas(Frame):
    def __init__(self, w=None, h=None, bg: WidgetBg = None) -> None:
        super().__init__()
        # A Canvas is a page ROOT — it must never become a child of whatever widget context happens
        # to be open. Widget.__init__ auto-adds every new widget to the enclosing `with` block, so
        # building a Canvas inside one (e.g. a drawer composing a sub-badge inline, which is the
        # `with Canvas(): with VSplit():` idiom every drawer uses) would silently adopt it into the
        # outer layout and corrupt it.
        parent = Widget.get_current_widget()
        if parent is not None and self in parent.items:
            parent.items.remove(self)
            self.parent = None

        self.set_size((w, h))
        self.set_bg(bg)
        self.set_margin(0)

    async def get_img(self, scale: float | None = None, cache_key: str | None = None) -> Image.Image:
        t = datetime.now()
        size = self._get_self_size()
        size_limit = CANVAS_SIZE_LIMIT
        assert size[0] * size[1] <= size_limit[0] * size_limit[1], f"Canvas size is too large ({size[0]}x{size[1]})"
        await prefetch_asset_refs(self)
        p = Painter(size=size)
        self.draw(p)
        img = await p.get(cache_key)
        if scale:
            img = img.resize((int(size[0] * scale), int(size[1] * scale)), Image.Resampling.BILINEAR)
        if DEBUG:
            logging.debug(f"Canvas drawn in {(datetime.now() - t).total_seconds():.3f}s, size={size}")
            pass
        return img

    def get_img_sync(self, scale: float | None = None) -> Image.Image:
        """Render the tree with Pillow from SYNCHRONOUS code (no pool offload, no prefetch).

        Same draw path as :meth:`get_img` — ``Painter._execute`` is the static, sync entry that
        one also ends in — so a tree renders identically either way. It is for callers that are
        already inside a worker/sync context (e.g. the custom-profile renderer composing an honor
        badge); lazy ``AssetImageRef``s resolve inline in the paste impls instead of being
        prefetched concurrently, so prefer :meth:`get_img` from async code."""
        size = self._get_self_size()
        size_limit = CANVAS_SIZE_LIMIT
        assert size[0] * size[1] <= size_limit[0] * size_limit[1], f"Canvas size is too large ({size[0]}x{size[1]})"
        p = Painter(size=size)
        self.draw(p)
        img = Painter._execute(p.operations, None, size)
        if scale:
            img = img.resize((int(size[0] * scale), int(size[1] * scale)), Image.Resampling.BILINEAR)
        return img


def parse_colored_text_segments(s: str) -> list[Seg]:
    raw_text = s
    try:
        segs: list[Seg] = [{"text": "", "color": None}]
        while True:
            i = s.find("<#")
            if i == -1:
                segs[-1]["text"] += s
                break
            j = s.find(">", i)
            if j == -1:
                raise ValueError("颜色代码标签未闭合")
            segs[-1]["text"] += s[:i]
            code = s[i + 2 : j]
            if len(code) == 6:
                r, g, b = int(code[:2], 16), int(code[2:4], 16), int(code[4:], 16)
            elif len(code) == 3:
                r, g, b = int(code[0], 16) * 17, int(code[1], 16) * 17, int(code[2], 16) * 17
            else:
                raise ValueError(f"颜色代码格式错误: {code}")
            segs.append({"text": "", "color": (r, g, b)})
            s = s[j + 1 :]
    except Exception:
        return [{"text": raw_text, "color": None}]
    return [seg for seg in segs if seg["text"]]


# 由带颜色代码的字符串获取彩色文本组件
def colored_text_box(
    s: str, style: TextStyle, padding=2, use_shadow=False, shadow_color=SHADOW, **text_box_kwargs
) -> HSplit:
    segs = parse_colored_text_segments(s)

    with HSplit().set_padding(padding).set_sep(0) as hs:
        for seg in segs:
            text, color = seg["text"], seg["color"]
            if text:
                if not use_shadow:
                    color_style = deepcopy(style)
                    if color is not None:
                        r, g, b = color
                        color_style.color = (r, g, b, 255)
                    TextBox(text, style=color_style, **text_box_kwargs).set_padding(0)
                else:
                    font = style.font
                    font_size = style.size
                    c1 = color if color else style.color
                    c2 = shadow_color
                    draw_shadowed_text(text, font, font_size, c1, c2, content_align="l", padding=0, **text_box_kwargs)
    return hs


# 绘制带阴影的文本
def draw_shadowed_text(
    text: str,
    font: str,
    font_size: int,
    c1: Color,
    c2: Color = SHADOW,
    offset: int | tuple[int, int] = 2,
    w: int | None = None,
    h: int | None = None,
    content_align: str = "c",
    padding: int = 2,
    **textbox_kwargs,
) -> Frame:
    if isinstance(offset, int):
        offset = (offset, offset)
    with Frame().set_size((w, h)).set_content_align(content_align) as frame:
        if c2:
            TextBox(text, TextStyle(font=font, size=font_size, color=c2), **textbox_kwargs).set_offset(
                offset
            ).set_padding(padding)
        TextBox(text, TextStyle(font=font, size=font_size, color=c1), **textbox_kwargs).set_padding(padding)
    return frame


if __name__ == "__main__":

    async def main():
        test_img = LinearGradient((200, 200, 255, 255), (255, 200, 200, 255), (0, 0), (1, 1)).get_img((100, 100))
        with Canvas(bg=FillBg((255, 0, 0, 255))).set_padding(16) as canvas:
            with (
                VSplit()
                .set_padding(16)
                .set_sep(8)
                .set_bg(RoundRectBg((255, 255, 255, 150), 8, blur_glass=True))
                .set_item_align("r")
                .set_content_align("r")
            ):
                ImageBox(test_img, image_size_mode="fit").set_size((200, 200)).set_padding(10)
                TextBox("Hello World", TextStyle(font=DEFAULT_FONT, size=20, color=BLACK), line_count=1)
                colored_text_box("<#FF0000>Hello <#00FF00>World", TextStyle(font=DEFAULT_FONT, size=20, color=BLACK))
                draw_shadowed_text(
                    "Hello World", DEFAULT_FONT, 20, (0, 0, 0), (255, 255, 255), content_align="c", padding=10
                )
                with Grid(col_count=5):
                    for i in range(5 * 5):
                        with HSplit().set_sep(5).set_bg(RoundRectBg((255, 255, 255, 150), 4)):
                            TextBox(f"Item {i + 1}", TextStyle(font=DEFAULT_FONT, size=16, color=BLACK))
                            ImageBox(test_img, image_size_mode="fit").set_size((20, 20))
        (await canvas.get_img()).save("sandbox/test.png")

    import asyncio

    asyncio.run(main())
