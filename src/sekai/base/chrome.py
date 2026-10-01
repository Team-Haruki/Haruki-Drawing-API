"""The house style shared by the redesigned pages: ink colours, text styles, chips, panels and section headers.

Deck, MySekai and misc pages draw the same furniture; keeping it here means a chip on one page is a chip on
every page (same ink centring, same parity bump) and a fix lands everywhere at once.
"""

from __future__ import annotations

from src.settings import DEFAULT_BOLD_FONT, DEFAULT_FONT, DEFAULT_HEAVY_FONT

from .draw import roundrect_bg
from .font_metrics import get_layout_font
from .paint_types import WHITE
from .plot import HSplit, RoundRectBg, Spacer, TextBox, TextStyle, VSplit
from .text_layout import get_text_size, ink_centered_text_offset_y

Color = tuple[int, int, int, int]

INK: Color = (34, 36, 50, 255)
TEXT: Color = (62, 64, 80, 255)
DIM: Color = (116, 118, 134, 255)
FAINT: Color = (158, 160, 174, 255)
RED: Color = (214, 64, 84, 255)
GREEN: Color = (38, 150, 90, 255)
AMBER: Color = (205, 120, 10, 255)
GREY: Color = (140, 144, 156, 255)
SLATE: Color = (112, 122, 146, 255)
BLUE: Color = (64, 132, 226, 255)

TITLE_STYLE = TextStyle(font=DEFAULT_HEAVY_FONT, size=30, color=INK)
SUBTITLE_STYLE = TextStyle(font=DEFAULT_FONT, size=16, color=DIM)
SECTION_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=(40, 44, 64, 255))
CAPTION_STYLE = TextStyle(font=DEFAULT_FONT, size=15, color=DIM)
PILL_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=17, color=TEXT)
CHIP_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=13, color=WHITE)
NOTE_STYLE = TextStyle(font=DEFAULT_FONT, size=15, color=DIM)

PANEL_PAD = 14
PANEL_SEP = 12
PILL_H = 48
PILL_FILL: Color = (255, 255, 255, 185)


def alpha(color, value: int) -> Color:
    return (color[0], color[1], color[2], value)


def mix(color, other, t: float) -> Color:
    """``color`` moved ``t`` of the way towards ``other`` (alpha from ``color``)."""
    return (
        round(color[0] + (other[0] - color[0]) * t),
        round(color[1] + (other[1] - color[1]) * t),
        round(color[2] + (other[2] - color[2]) * t),
        color[3] if len(color) > 3 else 255,
    )


def text_w(style: TextStyle, text: str) -> int:
    return get_text_size(get_layout_font(style.font, style.size), text)[0]


def ink(box: TextBox) -> TextBox:
    """Centre a one-line box's ink (not its em box) vertically, so every text in a chip row shares one axis.

    CJK ink hangs ~2 px below the em-box centre while digits and Latin caps sit higher, so a plain
    ``TextBox`` next to an ink-centred chip looks low; measure the real glyph bounds instead."""
    style = box.style
    # Thousands separators hang below the baseline; measuring them would lift a number above its neighbours.
    measured = box.text.replace(",", "") or box.text
    return box.set_text_offset((0, ink_centered_text_offset_y(style.font, style.size, measured, style.size)))


def fit_style(text: str, style: TextStyle, width: int, min_size: int = 10) -> TextStyle:
    """Step the font size down until ``text`` fits ``width`` on one line: numbers shrink, never lose digits."""
    size = style.size
    while size > min_size and text_w(style.replace(size=size), text) + 4 > width:
        size -= 1
    return style.replace(size=size) if size != style.size else style


def fitted(text: str, style: TextStyle, width: int, *, align: str = "c", min_size: int = 10) -> TextBox:
    """A fixed-width one-line value; past ``min_size`` it ends in "..." rather than overflowing."""
    return ink(
        TextBox(text, fit_style(text, style, width, min_size), overflow="shrink").set_w(width)
    ).set_content_align(align)


def chip(
    text: str,
    fill,
    *,
    style: TextStyle = CHIP_STYLE,
    radius: int = 9,
    padding=(8, 3),
    stroke=None,
    stroke_width: int = 1,
    max_w: int | None = None,
) -> TextBox:
    """A rounded label chip whose ink sits exactly in the middle of the fill.

    Ink centring alone leaves half a pixel over whenever the chip and the ink differ in height parity
    (a 13 px tall "组" in a 20 px chip), and the rounding pushes CJK a pixel low. Such chips grow by
    1 px so the gaps above and below the tallest glyph are equal; digits then sit at most 0.5 px high.

    ``max_w`` caps the chip: a longer text is clipped with "..." instead of overflowing its row."""
    pad_x, pad_y = padding
    font = get_layout_font(style.font, style.size)
    # Thousands separators hang below the baseline; measuring them would lift a number above its neighbours.
    _, top, _, bottom = font.getbbox(text.replace(",", "") or text)
    ink_h = bottom - top
    height = style.size + 2 * pad_y
    height += (height - ink_h) % 2
    # Painter.text puts the baseline ink_height("哇") below the line top; getbbox is relative to the ascender top.
    ink_top = get_text_size(font, "哇")[1] + top - font.getmetrics()[0]
    box = (
        TextBox(text, style, overflow="shrink" if max_w is not None else "clip")
        .set_padding((pad_x, pad_y))
        .set_h(height)
        .set_content_align("lt")
        .set_text_offset((0, (height - ink_h) // 2 - pad_y - ink_top))
        .set_bg(RoundRectBg(fill, radius, stroke=stroke, stroke_width=stroke_width, blur_glass=False))
    )
    if max_w is not None:
        natural = get_text_size(font, text)[0] + 4 + 2 * pad_x
        if natural > max_w:
            box.set_w(max(max_w, 2 * pad_x + 4))
    return box


_with_alpha = alpha


def soft_chip(text: str, color, *, size: int = 12, radius: int = 5, padding=(4, 1), alpha: int = 34) -> TextBox:
    """A pale chip tinted with ``color`` and lettered in it."""
    style = TextStyle(font=DEFAULT_BOLD_FONT, size=size, color=color)
    return chip(text, _with_alpha(color, alpha), style=style, radius=radius, padding=padding)


def panel(width: int) -> VSplit:
    return (
        VSplit()
        .set_w(width)
        .set_content_align("lt")
        .set_item_align("lt")
        .set_sep(PANEL_SEP)
        .set_padding(PANEL_PAD)
        .set_bg(roundrect_bg(alpha=80))
    )


def section_header(title: str, accent, *, chips=(), soft_chips=(), captions=()) -> None:
    """Accent bar + title + chips + dim captions: the header of every panel on these pages."""
    with HSplit().set_content_align("l").set_item_align("c").set_sep(10).set_padding(0):
        Spacer(w=6, h=24).set_bg(RoundRectBg(accent, 3, blur_glass=False))
        ink(TextBox(title, SECTION_STYLE))
        for text, fill in chips:
            chip(text, fill)
        for text, color in soft_chips:
            soft_chip(text, color, size=14, radius=9, padding=(8, 2), alpha=40)
        for caption in captions:
            if caption:
                ink(TextBox(caption, CAPTION_STYLE))


def pill() -> HSplit:
    """A white header pill (icon + text) of the shared height."""
    return (
        HSplit()
        .set_h(PILL_H)
        .set_content_align("l")
        .set_item_align("c")
        .set_sep(8)
        .set_padding((10, 0))
        .set_bg(RoundRectBg(PILL_FILL, 12, blur_glass=False))
    )
