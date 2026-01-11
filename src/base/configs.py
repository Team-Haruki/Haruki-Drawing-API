from pathlib import Path
import yaml
from typing import Dict, Any
def load_configs(path: str) -> None:
    # 预声明的配置文件变量和全局变量
    config_data: Dict[str, Any] = {}
    global ASSETS_BASE_DIR, \
        RESULT_ASSET_PATH, \
        FONT_DIR, \
        DEFAULT_FONT, \
        DEFAULT_BOLD_FONT, \
        DEFAULT_HEAVY_FONT, \
        DEFAULT_EMOJI_FONT
    # 读取配置文件
    with open(path, 'r', encoding='utf-8') as f:
        config_data = yaml.safe_load(f)
    # 资产的配置
    assets_config: Dict[str, Any] = config_data.get('assets', {})
    ASSETS_BASE_DIR = Path(assets_config.get("base_dir"))
    RESULT_ASSET_PATH = assets_config.get('result_asset_path')
    # 字体的配置
    font_config: Dict[str, Any] = config_data.get('font', {})
    FONT_DIR = Path(font_config.get('dir'))
    default_fonts: Dict[str, Any] = font_config.get('default')
    DEFAULT_FONT = default_fonts.get('default')
    DEFAULT_BOLD_FONT = default_fonts.get('bold')
    DEFAULT_HEAVY_FONT = default_fonts.get('heavy')
    DEFAULT_EMOJI_FONT = default_fonts.get('emoji')

ASSETS_BASE_DIR = Path('/Users/deseer/PycharmProjects/Haruki-Drawing-API/data')

# music.drawer
RESULT_ASSET_PATH = 'lunabot_static_images'

FONT_DIR = "/Users/deseer/PycharmProjects/Haruki-Drawing-API/data"
DEFAULT_FONT = "SourceHanSansSC-Regular"
DEFAULT_BOLD_FONT = "SourceHanSansSC-Bold"
DEFAULT_HEAVY_FONT = "SourceHanSansSC-Heavy"
DEFAULT_EMOJI_FONT = "EmojiOneColor-SVGinOT"

# 直接调用来覆盖
load_configs("/home/xmlq/codes/pycodes/Haruki-Drawing-API/configs.yaml")