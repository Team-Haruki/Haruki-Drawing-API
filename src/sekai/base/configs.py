import logging
import os
from pathlib import Path
from typing import Any

import yaml

# Get project root directory
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def load_configs(path: str | None = None) -> None:
    """Load configuration from YAML file.

    Args:
        path: Path to config file. If None or file doesn't exist, uses defaults.
    """
    # 预声明的配置文件变量和全局变量
    config_data: dict[str, Any] = {}
    global \
        ASSETS_BASE_DIR, \
        RESULT_ASSET_PATH, \
        TMP_PATH, \
        TRI_PATHS, \
        FONT_DIR, \
        DEFAULT_FONT, \
        DEFAULT_BOLD_FONT, \
        DEFAULT_HEAVY_FONT, \
        DEFAULT_EMOJI_FONT, \
        DEFAULT_THREAD_POOL_SIZE, \
        SCREENSHOT_API_PATH

    # Resolve config path
    if path is None:
        config_path = _PROJECT_ROOT / "configs.yaml"
    elif not os.path.isabs(path):
        config_path = _PROJECT_ROOT / path
    else:
        config_path = Path(path)

    # Try to load config file, skip if not found
    if not config_path.exists():
        logging.warning(f"Config file not found at: {config_path}, using defaults.")
        return

    # 读取配置文件
    try:
        with open(config_path, encoding="utf-8") as f:
            config_data = yaml.safe_load(f)
    except Exception as e:
        logging.error(f"Error loading config file: {e}")
        return

    # Use a helper to resolve paths relative to project root if they are relative
    def resolve_path(p: str) -> Path | None:
        if not p:
            return None
        path_obj = Path(p)
        if path_obj.is_absolute():
            return path_obj
        return (_PROJECT_ROOT / path_obj).resolve()

    # 资产的配置
    assets_config: dict[str, Any] = config_data.get("assets", {})
    if assets_config.get("base_dir"):
        ASSETS_BASE_DIR = resolve_path(assets_config.get("base_dir"))
    if assets_config.get("result_asset_path"):
        RESULT_ASSET_PATH = assets_config.get("result_asset_path")
    TMP_PATH = assets_config.get("tmp_path") or TMP_PATH
    if assets_config.get("tri_paths"):
        TRI_PATHS = [str(resolve_path(p)) for p in assets_config.get("tri_paths")]

    # 字体的配置
    font_config: dict[str, Any] = config_data.get("font", {})
    if font_config.get("dir"):
        FONT_DIR = resolve_path(font_config.get("dir"))
    default_fonts: dict[str, Any] = font_config.get("default", {})
    if default_fonts.get("default"):
        DEFAULT_FONT = default_fonts.get("default")
    if default_fonts.get("bold"):
        DEFAULT_BOLD_FONT = default_fonts.get("bold")
    if default_fonts.get("heavy"):
        DEFAULT_HEAVY_FONT = default_fonts.get("heavy")
    if default_fonts.get("emoji"):
        DEFAULT_EMOJI_FONT = default_fonts.get("emoji")
    # 默认线程池大小
    DEFAULT_THREAD_POOL_SIZE = int(config_data.get("default_thread_pool_size") or DEFAULT_THREAD_POOL_SIZE)
    # 截图微服务地址
    SCREENSHOT_API_PATH = config_data.get("screenshot_api_path") or SCREENSHOT_API_PATH


# Default values
ASSETS_BASE_DIR = _PROJECT_ROOT / "data"

# music.drawer
RESULT_ASSET_PATH = "lunabot_static_images"

# musci chart
TMP_PATH = "tmp"

TRI_PATHS = [
    str(ASSETS_BASE_DIR / "lunabot_static_images/triangle/tri1.png"),
    str(ASSETS_BASE_DIR / "lunabot_static_images/triangle/tri2.png"),
    str(ASSETS_BASE_DIR / "lunabot_static_images/triangle/tri3.png"),
]

FONT_DIR = _PROJECT_ROOT / "data"
DEFAULT_FONT = "SourceHanSansSC-Regular"
DEFAULT_BOLD_FONT = "SourceHanSansSC-Bold"
DEFAULT_HEAVY_FONT = "SourceHanSansSC-Heavy"
DEFAULT_EMOJI_FONT = "EmojiOneColor-SVGinOT"

DEFAULT_THREAD_POOL_SIZE = 8

SCREENSHOT_API_PATH = "http://localhost:18080/screenshot"


# Try to load configs (won't fail if file doesn't exist)
load_configs()
