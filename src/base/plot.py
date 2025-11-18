import threading
import contextvars

from copy import deepcopy
from datetime import datetime
from types import TracebackType
from dataclasses import dataclass
from typing import Union, Type, Callable, TypedDict, Self, Optional
from PIL import Image, ImageFilter, ImageFont, ImageEnhance


from .painter import (
    Color,
    Painter,
    get_font_desc,
    get_font,
    FontDesc,
    get_text_size,
    SHADOW,
    LinearGradient,
    ALIGN_MAP,
    TRANSPARENT,
    DEFAULT_FONT,
    BLACK,
)


DEBUG = False
CANVAS_SIZE_LIMIT = [4096, 4096]

DEFAULT_PADDING = 0
DEFAULT_MARGIN = 0
DEFAULT_SEP = 8


# =========================== 背景 =========================== #


class WidgetBg:
    def __init__(self) -> None:
        pass

    def draw(self, p: Painter) -> None:
        raise NotImplementedError()


class FillBg(WidgetBg):
    def __init__(self, fill: Color, stroke: Color = None, stroke_width: int = 1) -> None:
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
        stroke: Color = None,
        stroke_width: int = 1,
        corners: tuple[bool, bool, bool, bool] = (True, True, True, True),
        blur_glass: bool = False,
        blur_glass_kwargs: dict = None,
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
        self, img: Union[str, Image.Image], align: str = "c", mode: str = "fit", blur: bool = False, fade: float = 0.1
    ) -> None:
        super().__init__()
        if isinstance(img, str):
            self.img = Image.open(img)
        else:
            self.img = img
        assert align in ALIGN_MAP
        self.align = align
        assert mode in ("fit", "fill", "fixed", "repeat")
        self.mode = mode
        if blur:
            self.img = self.img.filter(ImageFilter.GaussianBlur(radius=3))
        if fade > 0:
            self.img = ImageEnhance.Brightness(self.img).enhance(1 - fade)

    def draw(self, p: Painter) -> None:
        if self.mode == "fit":
            ha, va = ALIGN_MAP[self.align]
            scale = max(p.w / self.img.size[0], p.h / self.img.size[1])
            w, h = int(self.img.size[0] * scale), int(self.img.size[1] * scale)
            if va == "c":
                y = (p.h - h) // 2
            elif va == "t":
                y = 0
            else:
                y = p.h - h
            if ha == "c":
                x = (p.w - w) // 2
            elif ha == "l":
                x = 0
            else:
                x = p.w - w
            p.paste(self.img, (x, y), (w, h))
        if self.mode == "fill":
            p.paste(self.img, (0, 0), p.size)
        if self.mode == "fixed":
            ha, va = ALIGN_MAP[self.align]
            if va == "c":
                y = (p.h - self.img.size[1]) // 2
            elif va == "t":
                y = 0
            else:
                y = p.h - self.img.size[1]
            if ha == "c":
                x = (p.w - self.img.size[0]) // 2
            elif ha == "l":
                x = 0
            else:
                x = p.w - self.img.size[0]
            p.paste(self.img, (x, y))
        if self.mode == "repeat":
            w, h = self.img.size
            for y in range(0, p.h, h):
                for x in range(0, p.w, w):
                    p.paste(self.img, (x, y))


class RandomTriangleBg(WidgetBg):
    def __init__(self, time_color: bool, main_hue: float = None, size_fixed_rate: float = 0.0) -> None:
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

    def __enter__(self)-> Self:
        local = self._thread_local.get()
        if local is None:
            local = threading.local()
            local.w_stack = []
        local.w_stack.append(self)
        self._thread_local.set(local)
        return self

    def __exit__(
        self,
        exc_type: Type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        local = self._thread_local.get()
        assert local is not None and local.w_stack[-1] == self
        local.w_stack.pop()
        if not local.w_stack:
            self._thread_local.set(None)

    def add_item(self, item: Self) -> None:
        raise NotImplementedError()

    def set_parent(self, parent: Self | None)-> Self:
        self.parent = parent
        return self

    def set_content_align(self, align: str)-> Self:
        if align not in ALIGN_MAP:
            raise ValueError("Invalid align")
        self.content_h_align, self.content_v_align = ALIGN_MAP[align]
        return self

    def set_margin(self, margin: Union[int, tuple[int, int]])-> Self:
        if isinstance(margin, int):
            self.v_margin = margin
            self.h_margin = margin
        else:
            self.h_margin = margin[0]
            self.v_margin = margin[1]
        return self

    def set_padding(self, padding: Union[int, tuple[int, int]])-> Self:
        if isinstance(padding, int):
            self.v_padding = padding
            self.h_padding = padding
        else:
            self.h_padding = padding[0]
            self.v_padding = padding[1]
        return self

    def set_size(self, size: tuple[Optional[int], Optional[int]])-> Self:
        if not size:
            size = (None, None)
        self.w = size[0]
        self.h = size[1]
        return self

    def set_w(self, w: int)-> Self:
        self.w = w
        return self

    def set_h(self, h: int)-> Self:
        self.h = h
        return self

    def set_offset(self, offset: tuple[int, int])-> Self:
        self.offset = offset
        return self

    def set_offset_anchor(self, anchor: str)-> Self:
        if anchor not in ALIGN_MAP:
            raise ValueError("Invalid anchor")
        self.offset_x_anchor, self.offset_y_anchor = ALIGN_MAP[anchor]
        return self

    def set_bg(self, bg: WidgetBg)-> Self:
        self.bg = bg
        return self

    def set_omit_parent_bg(self, omit: bool)-> Self:
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
                        f"Content size is too large with ({content_w}, {content_h}) > ({content_w_limit}, {content_h_limit})"
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
        assert cx is not None and cy is not None
        return cx, cy

    def _draw_self(self, p: Painter) -> None:
        if DEBUG:
            import random

            color = (random.randint(0, 200), random.randint(0, 200), random.randint(0, 200), 255)
            p.rect((0, 0), (p.w, p.h), TRANSPARENT, stroke=color, stroke_width=2)
            s = f"{self.__class__.__name__}({p.w},{p.h})"
            s += f"self={self._get_self_size()}"
            s += f"content={self._get_content_size()}"
            p.text(s, (3, 3), font=get_font_desc(DEFAULT_FONT, 16), fill=color)
            print(f"Draw {self.__class__.__name__} at {p.offset} size={p.size}")

        if self.bg:
            self.bg.draw(p)

        for draw_func in self.draw_funcs:
            draw_func(self, p)

    def _draw_content(self, p: Painter) -> None:
        pass

    def add_draw_func(self, draw_func: Callable[[Self, Painter], None])-> Self:
        self.draw_funcs.append(draw_func)
        return self

    def clear_draw_funcs(self)-> Self:
        self.draw_funcs.clear()
        return self

    def draw(self, p: Painter) -> None:
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
    def __init__(self, items: list[Widget] = None) -> None:
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
        items: list[Widget] = None,
        ratios: list[float] = None,
        sep: int = DEFAULT_SEP,
        item_size_mode: str = "fixed",
        item_align: str = "c",
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
            raise ValueError("Invalid align")
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

    def set_item_align(self, align: str) -> Self:
        if align not in ALIGN_MAP:
            raise ValueError("Invalid align")
        self.item_h_align, self.item_valign = ALIGN_MAP[align]
        return self

    def set_sep(self, sep: int) -> Self:
        self.sep = sep
        return self

    def set_ratios(self, ratios: list[float]) -> Self:
        self.ratios = ratios
        return self

    def set_item_size_mode(self, mode: str) -> Self:
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
                iw, ih = item._get_self_size()
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
        items: list[Widget] = None,
        ratios: list[float] = None,
        sep: int = DEFAULT_SEP,
        item_size_mode: str = "fixed",
        item_align: str = "c",
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
            raise ValueError("Invalid align")
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

    def set_item_align(self, align: str) -> Self:
        if align not in ALIGN_MAP:
            raise ValueError("Invalid align")
        self.item_h_align, self.item_valign = ALIGN_MAP[align]
        return self

    def set_sep(self, sep: int) -> Self:
        self.sep = sep
        return self

    def set_ratios(self, ratios: list[float]) -> Self:
        self.ratios = ratios
        return self

    def set_item_size_mode(self, mode: str) -> Self:
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
                iw, ih = item._get_self_size()
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
        items: list[Widget] = None,
        row_count: int = None,
        col_count: int = None,
        item_size_mode: str = "fixed",
        item_align: str = "c",
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
        assert not (self.row_count and self.col_count), "Either row_count or col_count should be None"
        assert item_size_mode in ("expand", "fixed")
        self.item_size_mode = item_size_mode
        self.h_sep = h_sep
        self.v_sep = v_sep
        if item_align not in ALIGN_MAP:
            raise ValueError("Invalid align")
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

    def set_item_align(self, align: str) -> Self:
        if align not in ALIGN_MAP:
            raise ValueError("Invalid align")
        self.item_h_align, self.item_valign = ALIGN_MAP[align]
        return self

    def set_sep(self, h_sep: int = None, v_sep: int = None) -> Self:
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

    def set_item_size_mode(self, mode: str) -> Self:
        assert mode in ("expand", "fixed")
        self.item_size_mode = mode
        return self

    def set_item_bg(self, bg: WidgetBg) -> Self:
        self.item_bg = bg
        return self

    def _get_grid_rc_and_size(self) -> tuple[tuple[int, int], tuple[int, int]]:
        r, c = self.row_count, self.col_count
        assert r and not c or c and not r, "Either row_count or col_count should be None"
        if not r:
            r = (len(self.items) + c - 1) // c
        if not c:
            c = (len(self.items) + r - 1) // r
        if self.item_size_mode == "expand":
            assert self.w is not None and self.h is not None, "Expand mode requires width and height"
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


@dataclass
class TextStyle:
    font: str = DEFAULT_FONT
    size: int = 16
    color: tuple[int, int, int] | tuple[int, int, int, int] = BLACK
    use_shadow: bool = False
    shadow_offset: tuple[int, int] | int = 1
    shadow_color: tuple[int, int, int, int] = SHADOW


class TextBox(Widget):
    def __init__(
        self,
        text: str = "",
        style: TextStyle = None,
        line_count: int = None,
        line_sep: int = 2,
        wrap: bool = True,
        overflow: str = "shrink",
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

    def _get_pil_font(self) -> ImageFont:
        return get_font(self.style.font, self.style.size)

    def _get_font_desc(self) -> FontDesc:
        return get_font_desc(self.style.font, self.style.size)

    def _get_clip_text_to_width_idx(self, text: str, width: int, suffix: str = "") -> tuple[int, int] | None:
        font = self._get_pil_font()
        w, _ = get_text_size(font, text + suffix)
        if w <= width:
            return None
        l, r = 0, len(text)
        while l <= r:
            m = (l + r) // 2
            w, _ = get_text_size(font, text[:m] + suffix)
            if w < width:
                l = m + 1
            elif w > width:
                r = m - 1
            else:
                return m
        return r

    def _get_lines(self) -> list[str]:
        lines = self.text.split("\n")
        clipped_lines = []
        for line in lines:
            if self.w:
                w = self.w - self.h_padding * 2
                suffix = "..." if self.overflow == "shrink" else ""
                if self.wrap:
                    while True:
                        line_suffix = suffix if len(clipped_lines) == self.line_count - 1 else ""
                        clip_idx = self._get_clip_text_to_width_idx(line, w, line_suffix)
                        if clip_idx is None:
                            clipped_lines.append(line)
                            break
                        clipped_lines.append(line[:clip_idx] + line_suffix)
                        line = line[clip_idx:]
                        if len(clipped_lines) == self.line_count:
                            break
                else:
                    clip_idx = self._get_clip_text_to_width_idx(line, w, suffix)
                    if clip_idx is not None:
                        line = line[:clip_idx] + suffix
                    clipped_lines.append(line)
            else:
                clipped_lines.append(line)
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
            p.move_region((x, y), (lw, self.style.size))
            p.text(line, (0, 0), font=self._get_font_desc(), fill=self.style.color)
            p.restore_region()


class ImageBox(Widget):
    def __init__(
        self,
        image: Union[str, Image.Image],
        image_size_mode=None,
        size=None,
        use_alpha_blend=False,
        alpha_adjust=1.0,
        shadow=False,
        shadow_width=6,
        shadow_alpha=0.6,
    ) -> None:
        """
        image_size_mode: 'fit', 'fill', 'original'
        """
        super().__init__()
        self.image_size_mode = None
        self.use_alpha_blend = None
        self.alpha_adjust = None
        if isinstance(image, str):
            self.image = Image.open(image)
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

    def set_image(self, image: Union[str, Image.Image]) -> Self:
        if isinstance(image, str):
            self.image = Image.open(image)
        else:
            self.image = image
        return self

    def set_image_size_mode(self, mode: str) -> Self:
        assert mode in ("fit", "fill", "original")
        self.image_size_mode = mode
        return self

    def _get_content_size(self) -> tuple[int, int] | None:
        w, h = self.image.size
        if self.image_size_mode == "original":
            return w, h
        elif self.image_size_mode == "fit":
            assert self.w is not None or self.h is not None, "Fit mode requires width or height"
            tw = self.w - self.h_padding * 2 if self.w else 1000000
            th = self.h - self.v_padding * 2 if self.h else 1000000
            scale = min(tw / w, th / h)
            return int(w * scale), int(h * scale)
        elif self.image_size_mode == "fill":
            assert self.w is not None or self.h is not None, "Fill mode requires width or height"
            if self.w and self.h:
                return int(self.w - self.h_padding * 2), int(self.h - self.v_padding * 2)
            else:
                tw = self.w - self.h_padding * 2 if self.w else 1000000
                th = self.h - self.v_padding * 2 if self.h else 1000000
                scale = max(tw / w, th / h)
                return int(w * scale), int(h * scale)
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
            )
        else:
            p.paste(
                self.image,
                (0, 0),
                (w, h),
                use_shadow=self.shadow,
                shadow_width=self.shadow_width,
                shadow_alpha=self.shadow_alpha,
            )


class Spacer(Widget):
    def __init__(self, w: int = 1, h: int = 1) -> None:
        super().__init__()
        self.set_size((w, h))

    def _get_content_size(self) -> tuple[int, int]:
        return self.w - 2 * self.h_padding, self.h - 2 * self.v_padding

    def _draw_content(self, p: Painter) -> None:
        pass


class Canvas(Frame):
    def __init__(self, w=None, h=None, bg: WidgetBg = None) -> None:
        super().__init__()
        self.set_size((w, h))
        self.set_bg(bg)
        self.set_margin(0)

    async def get_img(self, scale: float = None, cache_key: str = None) -> Image.Image:
        t = datetime.now()
        size = self._get_self_size()
        size_limit = CANVAS_SIZE_LIMIT
        assert size[0] * size[1] <= size_limit[0] * size_limit[1], f"Canvas size is too large ({size[0]}x{size[1]})"
        p = Painter(size=size)
        self.draw(p)
        img = await p.get(cache_key)
        if scale:
            img = img.resize((int(size[0] * scale), int(size[1] * scale)), Image.Resampling.BILINEAR)
        if DEBUG:
            print(f"Canvas drawn in {(datetime.now() - t).total_seconds():.3f}s, size={size}")
        return img


# =========================== 控件函数 =========================== #
class Seg(TypedDict):
    text: str | None
    color: tuple[int, int, int] | None


# 由带颜色代码的字符串获取彩色文本组件
def colored_text_box(
    s: str, style: TextStyle, padding=2, use_shadow=False, shadow_color=SHADOW, **text_box_kwargs
) -> HSplit:
    try:
        segs: list[Seg] = [{"text": None, "color": None}]
        while True:
            i = s.find("<#")
            if i == -1:
                segs[-1]["text"] = s
                break
            j = s.find(">", i)
            segs[-1]["text"] = s[:i]
            code = s[i + 2 : j]
            if len(code) == 6:
                r, g, b = int(code[:2], 16), int(code[2:4], 16), int(code[4:], 16)
            elif len(code) == 3:
                r, g, b = int(code[0], 16) * 17, int(code[1], 16) * 17, int(code[2], 16) * 17
            else:
                raise ValueError(f"颜色代码格式错误: {code}")
            segs.append({"text": None, "color": (r, g, b)})
            s = s[j + 1 :]
    except Exception as e:
        segs = [{"text": s, "color": None}]

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
    offset: Union[int, tuple[int, int]] = 2,
    w: int = None,
    h: int = None,
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
