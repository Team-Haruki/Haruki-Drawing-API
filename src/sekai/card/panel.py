"""Bound the temporary raster cost of large Card Box backgrounds."""

from src.sekai.base.draw import roundrect_bg
from src.sekai.base.plot import RoundRectBg

# BlurGlass reserves four RGBA scratch surfaces in addition to the page.
# Keep individual panel effects below roughly 64 MiB at the normal card scale.
MAX_GLASS_PIXELS = 4_000_000


class CardBoxPanelBg(RoundRectBg):
    def draw(self, painter) -> None:
        width, height = painter.size
        if self.blur_glass and width * height > MAX_GLASS_PIXELS:
            painter.roundrect(
                (0, 0), painter.size, self.fill, self.radius, self.stroke, self.stroke_width, self.corners
            )
        else:
            super().draw(painter)


def card_box_panel_bg(**kwargs) -> RoundRectBg:
    base = roundrect_bg(**kwargs)
    return CardBoxPanelBg(
        fill=base.fill,
        radius=base.radius,
        stroke=base.stroke,
        stroke_width=base.stroke_width,
        corners=base.corners,
        blur_glass=base.blur_glass,
        blur_glass_kwargs=base.blur_glass_kwargs,
    )
