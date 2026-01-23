# Base module exports

# From painter.py
from .painter import color_code_to_rgb

# From configs.py
from .configs import (
    DEFAULT_FONT,
    DEFAULT_BOLD_FONT,
    DEFAULT_HEAVY_FONT,
    DEFAULT_EMOJI_FONT,
    ASSETS_BASE_DIR,
)

# From utils.py
from .utils import get_img_from_path

# From draw.py
from .draw import (
    BG_PADDING,
    SEKAI_BLUE_BG,
    roundrect_bg,
    add_watermark,
)

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
    'color_code_to_rgb',
    'DEFAULT_FONT',
    'DEFAULT_BOLD_FONT',
    'DEFAULT_HEAVY_FONT',
    'DEFAULT_EMOJI_FONT',
    'ASSETS_BASE_DIR',
    'get_img_from_path',
    'BG_PADDING',
    'SEKAI_BLUE_BG',
    'roundrect_bg',
    'add_watermark',
    'CHARACTER_COLOR_CODE',
]
