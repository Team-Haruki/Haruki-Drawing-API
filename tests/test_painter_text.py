from __future__ import annotations

import asyncio

from PIL import Image, ImageChops
import pytest

from src.sekai.base.painter import AdaptiveTextColor, FontDesc, LinearGradient, Painter, get_font
from src.settings import DEFAULT_FONT


@pytest.mark.parametrize(
    ("fill", "use_font_desc"),
    [
        ((20, 30, 40), True),
        ((20, 30, 40, 255), False),
        ((20, 30, 40, 128), False),
        (LinearGradient((255, 0, 0, 255), (0, 0, 255, 255), (0, 0), (1, 0)), False),
        (
            AdaptiveTextColor(
                pixelwise=False,
                light=(255, 255, 255, 128),
                dark=(0, 0, 0, 128),
                threshold=0.4,
            ),
            False,
        ),
        (AdaptiveTextColor(pixelwise=True), False),
    ],
)
def test_painter_text_fill_modes_render_pixels(fill, use_font_desc: bool) -> None:
    background = Image.new("RGBA", (160, 60), (25, 25, 25, 255))
    background.paste((235, 235, 235, 255), (80, 0, 160, 60))
    painter = Painter(background.copy())
    font = FontDesc(DEFAULT_FONT, 24) if use_font_desc else get_font(DEFAULT_FONT, 24)

    painter.text("Aa测试", (48, 12), font, fill=fill)
    rendered = asyncio.run(painter.get())

    assert ImageChops.difference(rendered, background).convert("RGB").getbbox() is not None
