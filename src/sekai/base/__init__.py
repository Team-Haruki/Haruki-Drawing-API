# Base module exports

from src.settings import (
    ASSETS_BASE_DIR,
    DEFAULT_BOLD_FONT,
    DEFAULT_EMOJI_FONT,
    DEFAULT_FONT,
    DEFAULT_HEAVY_FONT,
)

# From draw.py
from .draw import (
    BG_PADDING,
    SEKAI_BLUE_BG,
    add_watermark,
    roundrect_bg,
)
from .painter import color_code_to_rgb

# From utils.py
from .utils import get_img_from_path

# Character color codes
CHARACTER_COLOR_CODE = {
    1: "#33AAEE",
    2: "#FFDD44",
    3: "#EE6677",
    4: "#44CCBB",
    5: "#33DD99",
    6: "#BB88EE",
    7: "#FF6699",
    8: "#99CCFF",
    9: "#FFCC11",
    10: "#FF7711",
    11: "#FF5566",
    12: "#44BBFF",
    13: "#9955EE",
    14: "#FF66BB",
    15: "#FFDD00",
    16: "#FF9988",
    17: "#FF6688",
    18: "#FF8899",
    19: "#AADDFF",
    20: "#88DD55",
    21: "#FFAACC",
    22: "#0077DD",
    23: "#EE8855",
    24: "#EE8844",
    25: "#CC5533",
    26: "#777777",
}

__all__ = [
    "ASSETS_BASE_DIR",
    "BG_PADDING",
    "CHARACTER_COLOR_CODE",
    "DEFAULT_BOLD_FONT",
    "DEFAULT_EMOJI_FONT",
    "DEFAULT_FONT",
    "DEFAULT_HEAVY_FONT",
    "SEKAI_BLUE_BG",
    "add_watermark",
    "color_code_to_rgb",
    "get_img_from_path",
    "roundrect_bg",
]
