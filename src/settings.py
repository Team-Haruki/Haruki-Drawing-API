"""
Unified configuration system using pydantic-settings.

Usage:
    from src.settings import settings

    # Access configuration
    settings.assets.base_dir
    settings.font.default
    settings.server.port

    # Or use convenience exports
    from src.settings import ASSETS_BASE_DIR, DEFAULT_FONT
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_IMAGE_DIR = "static_images"


class AssetsSettings(BaseModel):
    """资产文件配置"""

    base_dir: Path = Path("data")
    result_asset_path: str = STATIC_IMAGE_DIR
    tmp_path: str = "tmp"
    tri_paths: list[str] = []

    @field_validator("base_dir", mode="before")
    @classmethod
    def resolve_base_dir(cls, v: str | Path) -> Path:
        path = Path(v)
        if not path.is_absolute():
            return (PROJECT_ROOT / path).resolve()
        return path


class FontSettings(BaseModel):
    """字体配置"""

    dir: Path = Path("data")
    default: str = "SourceHanSansSC-Regular"
    bold: str = "SourceHanSansSC-Bold"
    heavy: str = "SourceHanSansSC-Heavy"
    emoji: str = "EmojiOneColor-SVGinOT"

    @field_validator("dir", mode="before")
    @classmethod
    def resolve_font_dir(cls, v: str | Path) -> Path:
        path = Path(v)
        if not path.is_absolute():
            return (PROJECT_ROOT / path).resolve()
        return path


class ServerSettings(BaseModel):
    """服务器配置"""

    host: str = "0.0.0.0"
    port: int = 8000


class LoggingSettings(BaseModel):
    """日志配置"""

    level: str = "INFO"
    format: str = "[%(asctime)s][%(levelname)s][%(name)s] %(message)s"
    field_styles: dict = Field(
        default_factory=lambda: {
            "asctime": {"color": "green"},
            "levelname": {"color": "blue", "bold": True},
            "name": {"color": "magenta"},
            "message": {"color": 144, "bright": False},
        }
    )


class DrawingSettings(BaseModel):
    """画图配置"""

    thread_pool_size: int = 8
    isolated_worker_pool_size: int = 8  # 重任务隔离子进程池大小，仅用于高风险接口
    isolated_worker_queue_limit: int = 16  # 重任务排队上限（不含正在执行的 worker）
    isolated_worker_queue_timeout_seconds: int = 30  # 重任务排队超时（秒）
    request_hard_timeout_seconds: int = 180  # 单个重任务的硬超时（秒）
    overload_max_inflight_requests: int = 0  # 过载保护：允许的最大并发请求数，0 表示关闭
    overload_retry_after_seconds: int = 5  # 过载拒绝后的 Retry-After 秒数
    readiness_unhealthy_inflight_requests: int = 0  # readiness: inflight 达到该值时返回不健康，0 表示关闭
    readiness_unhealthy_rss_mb: int = 0  # readiness: RSS 达到该值时返回不健康，0 表示关闭
    readiness_unhealthy_asyncio_tasks: int = 0  # readiness: asyncio task 达到该值时返回不健康，0 表示关闭
    image_cache_size: int = 0  # 图片解码缓存条目数，0 表示关闭
    image_cache_max_mb: int = 0  # 图片解码缓存总内存上限（MB），0 表示关闭
    thumbnail_cache_size: int = 0  # 缩略图专用缓存条目数，0 表示关闭
    thumbnail_cache_max_mb: int = 0  # 缩略图专用缓存总内存上限（MB），0 表示关闭
    composed_image_cache_size: int = 0  # 合成图片缓存条目数，0 表示关闭
    composed_image_cache_max_mb: int = 0  # 合成图片缓存总内存上限（MB），0 表示关闭
    composed_image_cache_ttl_seconds: int = 7 * 24 * 3600  # 合成图片缓存 TTL（秒）
    use_process_pool: bool = False  # 是否启用进程池
    process_pool_workers: int = 4  # 进程池工作进程数
    process_pool_threshold: int = 2_000_000  # 像素阈值 (约 2000x1000)
    screenshot_api_path: str = "http://localhost:18080/screenshot"
    export_image_format: Literal["png", "jpg"] = "png"  # 导出图片格式
    jpg_quality: int = Field(default=85, ge=1, le=100)  # JPEG 压缩质量 (1-100)


class Settings(BaseSettings):
    """Main settings class."""

    model_config = SettingsConfigDict(
        env_prefix="HARUKI_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    assets: AssetsSettings = AssetsSettings()
    font: FontSettings = FontSettings()
    server: ServerSettings = ServerSettings()
    logging: LoggingSettings = LoggingSettings()
    drawing: DrawingSettings = DrawingSettings()

    @classmethod
    def from_yaml(cls, path: Path | None = None) -> "Settings":
        """Load settings from YAML file."""
        if path is None:
            path = PROJECT_ROOT / "configs.yaml"

        if not path.exists():
            return cls()

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        # Map old config structure to new
        mapped: dict = {}
        if "assets" in data:
            mapped["assets"] = data["assets"]
        if "font" in data:
            font_data = data["font"]
            mapped["font"] = {"dir": font_data.get("dir"), **font_data.get("default", {})}
        if "server" in data:
            mapped["server"] = data["server"]
        if "logging" in data:
            mapped["logging"] = data["logging"]

        # Drawing settings
        drawing: dict = {}
        if "default_thread_pool_size" in data:
            drawing["thread_pool_size"] = data["default_thread_pool_size"]
        if "screenshot_api_path" in data:
            drawing["screenshot_api_path"] = data["screenshot_api_path"]
        if "drawing" in data:
            drawing.update(data["drawing"])
        if drawing:
            mapped["drawing"] = drawing

        return cls(**mapped)


# Singleton instance
settings = Settings.from_yaml()


# ========== Convenience exports ========== #
# These allow `from src.settings import ASSETS_BASE_DIR` style imports

# Assets
ASSETS_BASE_DIR = settings.assets.base_dir
RESULT_ASSET_PATH = settings.assets.result_asset_path
TMP_PATH = settings.assets.tmp_path
TRI_PATHS = settings.assets.tri_paths or [
    str(ASSETS_BASE_DIR / RESULT_ASSET_PATH / "triangle/tri1.png"),
    str(ASSETS_BASE_DIR / RESULT_ASSET_PATH / "triangle/tri2.png"),
    str(ASSETS_BASE_DIR / RESULT_ASSET_PATH / "triangle/tri3.png"),
]

# Fonts
FONT_DIR = settings.font.dir
DEFAULT_FONT = settings.font.default
DEFAULT_BOLD_FONT = settings.font.bold
DEFAULT_HEAVY_FONT = settings.font.heavy
DEFAULT_EMOJI_FONT = settings.font.emoji

# Drawing
DEFAULT_THREAD_POOL_SIZE = settings.drawing.thread_pool_size
ISOLATED_WORKER_POOL_SIZE = settings.drawing.isolated_worker_pool_size
ISOLATED_WORKER_QUEUE_LIMIT = settings.drawing.isolated_worker_queue_limit
ISOLATED_WORKER_QUEUE_TIMEOUT_SECONDS = settings.drawing.isolated_worker_queue_timeout_seconds
REQUEST_HARD_TIMEOUT_SECONDS = settings.drawing.request_hard_timeout_seconds
OVERLOAD_MAX_INFLIGHT_REQUESTS = settings.drawing.overload_max_inflight_requests
OVERLOAD_RETRY_AFTER_SECONDS = settings.drawing.overload_retry_after_seconds
READINESS_UNHEALTHY_INFLIGHT_REQUESTS = settings.drawing.readiness_unhealthy_inflight_requests
READINESS_UNHEALTHY_RSS_MB = settings.drawing.readiness_unhealthy_rss_mb
READINESS_UNHEALTHY_ASYNCIO_TASKS = settings.drawing.readiness_unhealthy_asyncio_tasks
IMAGE_CACHE_SIZE = settings.drawing.image_cache_size
IMAGE_CACHE_MAX_BYTES = settings.drawing.image_cache_max_mb * 1024 * 1024
THUMB_CACHE_SIZE = settings.drawing.thumbnail_cache_size
THUMB_CACHE_MAX_BYTES = settings.drawing.thumbnail_cache_max_mb * 1024 * 1024
COMPOSED_IMAGE_CACHE_SIZE = settings.drawing.composed_image_cache_size
COMPOSED_IMAGE_CACHE_MAX_BYTES = settings.drawing.composed_image_cache_max_mb * 1024 * 1024
COMPOSED_IMAGE_CACHE_TTL_SECONDS = settings.drawing.composed_image_cache_ttl_seconds
SCREENSHOT_API_PATH = settings.drawing.screenshot_api_path
EXPORT_IMAGE_FORMAT = settings.drawing.export_image_format
JPG_QUALITY = settings.drawing.jpg_quality

# Server
SERVER_HOST = settings.server.host
SERVER_PORT = settings.server.port

# Logging
LOG_FORMAT = settings.logging.format
FIELD_STYLE = settings.logging.field_styles
