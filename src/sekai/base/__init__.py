# Base module exports

from importlib import import_module

# Keep metadata/reference imports usable without importing either renderer. Existing
# package-level names resolve lazily for backwards compatibility.
_LAZY_EXPORTS = {
    **dict.fromkeys(
        ("ASSETS_BASE_DIR", "DEFAULT_BOLD_FONT", "DEFAULT_EMOJI_FONT", "DEFAULT_FONT", "DEFAULT_HEAVY_FONT"),
        "src.settings",
    ),
    **dict.fromkeys(
        ("BG_PADDING", "SEKAI_BLUE_BG", "add_request_watermark", "add_watermark", "roundrect_bg"),
        "src.sekai.base.draw",
    ),
    "color_code_to_rgb": "src.sekai.base.paint_types",
    "get_img_from_path": "src.sekai.base.utils",
}


def __getattr__(name):
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module), name)


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
    "add_request_watermark",
    "add_watermark",
    "color_code_to_rgb",
    "get_img_from_path",
    "roundrect_bg",
]
