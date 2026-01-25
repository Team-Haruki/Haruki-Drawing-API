from pathlib import Path
import yaml
from typing import Dict, Any
import os

# Get project root directory
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

def load_configs(path: str = None) -> None:
    """Load configuration from YAML file.
    
    Args:
        path: Path to config file. If None or file doesn't exist, uses defaults.
    """
    # 预声明的配置文件变量和全局变量
    config_data: Dict[str, Any] = {}
    global ASSETS_BASE_DIR, \
        RESULT_ASSET_PATH, \
        TRI_PATHS, \
        FONT_DIR, \
        DEFAULT_FONT, \
        DEFAULT_BOLD_FONT, \
        DEFAULT_HEAVY_FONT, \
        DEFAULT_EMOJI_FONT
    
    # Resolve config path
    if path is None:
        config_path = _PROJECT_ROOT / "configs.yaml"
    elif not os.path.isabs(path):
        config_path = _PROJECT_ROOT / path
    else:
        config_path = Path(path)
    
    # Try to load config file, skip if not found
    if not config_path.exists():
        return
        
    # 读取配置文件
    with open(config_path, 'r', encoding='utf-8') as f:
        config_data = yaml.safe_load(f)
    # 资产的配置
    assets_config: Dict[str, Any] = config_data.get('assets', {})
    if assets_config.get("base_dir"):
        ASSETS_BASE_DIR = Path(assets_config.get("base_dir"))
    if assets_config.get('result_asset_path'):
        RESULT_ASSET_PATH = assets_config.get('result_asset_path')
    if assets_config.get('tri_paths'):
        TRI_PATHS = assets_config.get('tri_paths')
    # 字体的配置
    font_config: Dict[str, Any] = config_data.get('font', {})
    if font_config.get('dir'):
        FONT_DIR = Path(font_config.get('dir'))
    default_fonts: Dict[str, Any] = font_config.get('default', {})
    if default_fonts.get('default'):
        DEFAULT_FONT = default_fonts.get('default')
    if default_fonts.get('bold'):
        DEFAULT_BOLD_FONT = default_fonts.get('bold')
    if default_fonts.get('heavy'):
        DEFAULT_HEAVY_FONT = default_fonts.get('heavy')
    if default_fonts.get('emoji'):
        DEFAULT_EMOJI_FONT = default_fonts.get('emoji')

# Default values
ASSETS_BASE_DIR = Path('/data')

# music.drawer
RESULT_ASSET_PATH = 'lunabot_static_images'

TRI_PATHS = [
    '/Users/deseer/PycharmProjects/Haruki-Drawing-API/data/lunabot_static_images/triangle/tri1.png',
    '/Users/deseer/PycharmProjects/Haruki-Drawing-API/data/lunabot_static_images/triangle/tri2.png',
    '/Users/deseer/PycharmProjects/Haruki-Drawing-API/data/lunabot_static_images/triangle/tri3.png',
]

FONT_DIR = "/data"
DEFAULT_FONT = "SourceHanSansSC-Regular"
DEFAULT_BOLD_FONT = "SourceHanSansSC-Bold"
DEFAULT_HEAVY_FONT = "SourceHanSansSC-Heavy"
DEFAULT_EMOJI_FONT = "EmojiOneColor-SVGinOT"

# Try to load configs (won't fail if file doesn't exist)
load_configs()