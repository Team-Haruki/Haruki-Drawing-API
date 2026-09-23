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

import logging
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml

logger = logging.getLogger("src.settings")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_IMAGE_DIR = "static_images"

# Every key a provider block may carry (canonical names and accepted aliases, contract addendum A7).
# `endpoints` is handled separately: it is Cloud's per-node list and Drawing only borrows its first element.
_PROVIDER_KEYS = frozenset(
    {
        "provider",
        "name",
        "scheme",
        "kind",
        "endpoint",
        "tls",
        "bucket",
        "root",
        "prefix",
        "region",
        "access_key_id",
        "access_key",
        "secret_access_key",
        "secret_key",
        "base_url",
        "public_base_url",
        "public_read",
        "path_style",
        "options",
    }
)


class StorageProviderSettings(BaseModel):
    """Object-storage provider block, key names shared with Asset-Updater and Haruki-Cloud (addendum A7).

    `scheme` defaults to `fs` (not Asset-Updater's `s3`) so a zero-config service keeps today's local
    behaviour. `memory` is a Drawing-local test/smoke value. Secrets are `SecretStr` and never appear in
    `repr`, `model_dump()` or `describe()`.
    """

    model_config = ConfigDict(populate_by_name=True)

    provider: str = Field(default="", validation_alias=AliasChoices("provider", "name"))
    scheme: Literal["s3", "fs", "memory"] = Field(default="fs", validation_alias=AliasChoices("scheme", "kind"))
    endpoint: str = ""  # host[:port] or a full URL; `tls` decides the URL scheme of a bare host
    tls: bool = True
    bucket: str = ""  # "{region}" / "{server}" templated
    root: str = ""  # "{region}" templated; "" = bucket root (the configured value on both slots)
    prefix: str | None = None  # legacy alias of root; root wins when both are set
    region: str = "garage"
    access_key_id: SecretStr | None = Field(default=None, validation_alias=AliasChoices("access_key_id", "access_key"))
    secret_access_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("secret_access_key", "secret_key")
    )
    base_url: str = Field(default="", validation_alias=AliasChoices("base_url", "public_base_url"))
    public_read: bool = False
    path_style: bool = True
    options: dict[str, str] = {}

    @model_validator(mode="before")
    @classmethod
    def _normalise_provider_keys(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for key in list(data):
            if key == "endpoints" or key in _PROVIDER_KEYS:
                continue
            logger.warning("settings.provider_unknown_key key=%s", key)
            data.pop(key)
        if "endpoints" in data:
            endpoints = data.pop("endpoints")
            logger.warning("settings.provider_endpoints_ignored count=%s", _endpoint_count(endpoints))
            if not data.get("endpoint") and isinstance(endpoints, (list, tuple)) and endpoints:
                data["endpoint"] = str(endpoints[0])
        return data

    @field_validator("scheme", mode="before")
    @classmethod
    def _scheme_alias_value(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip().lower()
            if v in ("", "local"):
                return "fs"
        return v

    def describe(self) -> dict[str, str]:
        """Loggable identity of the provider — never credentials."""
        return {
            "provider": self.provider,
            "scheme": self.scheme,
            "endpoint": self.endpoint,
            "bucket": self.bucket,
            "root": self.root,
        }


def _endpoint_count(endpoints: Any) -> int:
    return len(endpoints) if isinstance(endpoints, (list, tuple)) else 1


class AssetMirrorProviderSettings(StorageProviderSettings):
    """Assets slot: object keys are `<region>-assets/<mode>/<rel>`, so `root` stays "" (addendum A9(7)).

    A subclass (not a default instance) so a partial env/YAML block keeps the slot defaults.
    """

    scheme: Literal["s3", "fs", "memory"] = Field(default="s3", validation_alias=AliasChoices("scheme", "kind"))
    bucket: str = "pjsk-assets"


class ArtifactProviderSettings(StorageProviderSettings):
    """Image-cache slot: object keys are `pjsk/<api_path>/<sha256>.<ext>`, `root` stays "" (addendum A9(8))."""

    scheme: Literal["s3", "fs", "memory"] = Field(default="s3", validation_alias=AliasChoices("scheme", "kind"))
    bucket: str = "image-cache"


class AssetMirrorSettings(BaseModel):
    """On-demand asset mirror (read side). Nothing is wired until `assets.source == "mirror"`."""

    provider: AssetMirrorProviderSettings = AssetMirrorProviderSettings()
    dir: str = "mirror"  # RELATIVE to assets.base_dir
    manifest_version: str = "v0"  # E5 path segment; assets.manifest_version wins when set (addendum B7)
    manifest_version_file: Path | None = None  # optional per-node file; its first line has top precedence
    manifest_version_poll_seconds: int = 30
    versions_keep: int = 2
    max_bytes: int = 8 * 1024**3  # 0 = unlimited
    max_entries: int = 200_000  # 0 = unlimited
    sweep_interval_seconds: int = 600
    fetch_timeout_seconds: float = 5.0  # total budget per ensure_local
    fetch_io_timeout_seconds: float = 5.0  # per opendal I/O op
    fetch_retries: int = 1
    fetch_concurrency: int = 8
    fetch_max_bytes: int = 64 * 1024**2
    negative_ttl_seconds: float = 60.0  # 0 disables the NotFound memo
    negative_memo_max: int = 32_768
    local_fallback: bool = True
    breaker_failures: int = 5
    breaker_open_seconds: float = 30.0
    tmp_max_age_seconds: int = 3600

    @field_validator("dir")
    @classmethod
    def _dir_must_stay_under_base(cls, v: str) -> str:
        normalised = v.replace("\\", "/")
        if not normalised.strip() or normalised.startswith("/") or Path(v).is_absolute():
            raise ValueError("assets.mirror.dir must be a non-empty path relative to assets.base_dir")
        if ".." in PurePosixPath(normalised).parts:
            raise ValueError("assets.mirror.dir must not contain '..'")
        return v


class UserUploadProviderSettings(StorageProviderSettings):
    """User-upload slot: Cloud's keys already carry the `user_upload/` prefix, so `root` stays "".

    A subclass (not a default instance) so a partial env/YAML block keeps the slot defaults.
    """

    scheme: Literal["s3", "fs", "memory"] = Field(default="s3", validation_alias=AliasChoices("scheme", "kind"))
    bucket: str = "user-upload"


class UserUploadSettings(BaseModel):
    """Cloud-written user uploads (profile backgrounds), read on demand from the `user-upload` bucket.

    Off by default: `bg_settings.img_path` keeps resolving under `assets.base_dir` exactly as before. With
    `enabled`, a `user_upload/profile_bg/...` path is read from the bucket first; a miss or a store failure
    falls back to the local file (`local_fallback`) and then to the default background, never a 500.
    """

    enabled: bool = False
    provider: UserUploadProviderSettings = UserUploadProviderSettings()
    fetch_timeout_seconds: float = 3.0  # total budget per read, retries included
    fetch_io_timeout_seconds: float = 3.0  # per opendal I/O op
    fetch_retries: int = 1
    fetch_concurrency: int = 8
    fetch_max_bytes: int = 8 * 1024**2  # Cloud caps a profile background at 1 MiB; larger objects are refused
    cache_size: int = 64  # the cache holds ENCODED bytes keyed by object key (Cloud names every upload uniquely)
    cache_max_mb: int = 32
    cache_ttl_seconds: float = 300.0  # 0 disables the cache
    local_fallback: bool = True  # after a bucket miss/failure, try today's <base_dir>/user_upload/... file


class AssetsSettings(BaseModel):
    """资产文件配置"""

    base_dir: Path = Path("data")
    result_asset_path: str = STATIC_IMAGE_DIR
    tmp_path: str = "tmp"
    tri_paths: list[str] = []
    source: Literal["local", "mirror"] = "local"
    manifest_version: str | None = None  # HARUKI_ASSETS__MANIFEST_VERSION (addendum B7)
    mirror: AssetMirrorSettings = AssetMirrorSettings()
    user_upload: UserUploadSettings = UserUploadSettings()

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
    readiness_unhealthy_rss_mb: int = 0  # readiness: 父进程 RSS 达到该值时返回不健康，0 表示关闭
    readiness_unhealthy_asyncio_tasks: int = 0  # readiness: asyncio task 达到该值时返回不健康，0 表示关闭
    # readiness: 容器内存用量达到硬限额的该百分比时返回不健康，0 表示关闭。
    # 这是唯一能看见 heavy worker 的内存信号（它们是独立进程，父进程 RSS 看不到），
    # 而且按百分比表达就不可能像绝对 MB 那样被配到硬限额之上、永远触发不了。
    readiness_unhealthy_cgroup_percent: int = 0
    image_cache_size: int = 0  # 图片解码缓存条目数，0 表示关闭
    image_cache_max_mb: int = 0  # 图片解码缓存总内存上限（MB），0 表示关闭
    thumbnail_cache_size: int = 0  # 缩略图专用缓存条目数，0 表示关闭
    thumbnail_cache_max_mb: int = 0  # 缩略图专用缓存总内存上限（MB），0 表示关闭
    composed_image_cache_size: int = 0  # 合成图片缓存条目数，0 表示关闭
    composed_image_cache_max_mb: int = 0  # 合成图片缓存总内存上限（MB），0 表示关闭
    composed_image_cache_ttl_seconds: int = 7 * 24 * 3600  # 合成图片缓存 TTL（秒）
    export_image_format: Literal["png", "jpg"] = "png"  # 导出图片格式
    jpg_quality: int = Field(default=85, ge=1, le=100)  # JPEG 压缩质量 (1-100)
    # Skia 门控:默认开启(2026-07-12 全端点真实数据对拍通过后切换)。扩展缺失时 fail-open
    # 回退 Pillow 并打 ERROR。开关一律不写入 configs.yaml,生产用 HARUKI_DRAWING__* 环境变量覆盖。
    use_skia_plot: bool = True  # plot.py widget 树端点的 IRPainter → Skia 渲染
    custom_profile_assets_dir: Path | None = None
    custom_profile_fonts_dir: Path | None = None
    custom_profile_tmp_font_metadata: Path | None = None
    custom_profile_shape_sprite_dir: Path | None = None
    custom_profile_unity_ui_sprite_dir: Path | None = None
    custom_profile_parallel_workers: int = 1
    # custom profile 是用户可控 Unity 场景。这里的限制是内存安全边界，不是性能调优项：
    # 单个异常 scale 曾在裁剪前触发多份全尺寸 float32 栅格并把无 cgroup 限额的宿主机 OOM。
    custom_profile_max_concurrent_requests: int = Field(default=1, ge=1)
    custom_profile_max_elements: int = Field(default=256, ge=1)
    custom_profile_max_scale: float = Field(default=8.0, gt=0)
    custom_profile_max_text_size: float = Field(default=1024.0, gt=0)
    custom_profile_max_text_length: int = Field(default=4096, ge=1)
    custom_profile_max_layer_pixels: int = Field(default=8 * 1024 * 1024, ge=1)
    custom_profile_max_scene_mb: int = Field(default=256, ge=1)
    # custom profile 进程级缓存(字形 SDF/轮廓、sprite/atlas)。与其他缓存键不同,默认即开启:
    # 该渲染器冷路径的 1.5s+ 就是这些缓存随请求丢弃造成的,归零任一对即禁用对应池(回滚开关)。
    custom_profile_glyph_cache_size: int = 4096  # 字形 SDF/轮廓缓存条目数(两池各自适用),0 表示关闭
    custom_profile_glyph_cache_max_mb: int = 64  # 字形缓存单池内存上限(MB),0 表示关闭
    custom_profile_sprite_cache_size: int = 512  # sprite/atlas 解码缓存条目数,0 表示关闭
    custom_profile_sprite_cache_max_mb: int = 128  # sprite/atlas 缓存内存上限(MB),0 表示关闭
    # Custom Profile Skia 失败的脱敏诊断:只落源码栈、异常指纹和分类计数,绝不落请求/卡片/资源路径。
    custom_profile_diagnostic_dir: Path | None = None
    custom_profile_diagnostic_retention_hours: int = Field(default=7 * 24, ge=1)
    custom_profile_diagnostic_max_files: int = Field(default=256, ge=1)
    # 请求体转储(采集对拍 payload/排障用):设为目录时把白名单路径前缀的原始请求 body 落盘。
    # 生产走 HARUKI_DRAWING__DEBUG_DUMP_REQUEST_DIR / _PATHS 短窗开启,采完即关。默认关闭。
    # (tmp 清扫器只删注册过的文件、不扫目录,dump 放哪都不会被清;独立目录只是整洁。)
    debug_dump_request_dir: Path | None = None
    debug_dump_request_paths: str = ""  # 逗号分隔的路径前缀白名单;空 = 不转储

    @field_validator(
        "custom_profile_assets_dir",
        "custom_profile_fonts_dir",
        "custom_profile_tmp_font_metadata",
        "custom_profile_shape_sprite_dir",
        "custom_profile_unity_ui_sprite_dir",
        "custom_profile_diagnostic_dir",
        "debug_dump_request_dir",
        mode="before",
    )
    @classmethod
    def resolve_optional_path(cls, v: str | Path | None) -> Path | None:
        if v is None or str(v).strip() == "":
            return None
        path = Path(v)
        if not path.is_absolute():
            return (PROJECT_ROOT / path).resolve()
        return path


class IndexSettings(BaseModel):
    """PostgreSQL render index (INSERT/SELECT only; Cloud owns the DDL)."""

    enabled: bool = True  # empty dsn == disabled (uploads still happen)
    dsn: SecretStr | None = None  # ENV ONLY — from_yaml drops a YAML value with a WARNING
    pool_min_size: int = 0
    pool_max_size: int = 4
    connect_timeout_seconds: float = 2.0
    command_timeout_seconds: float = 2.0
    connect_retry_seconds: float = 30.0


class StorageSettings(BaseModel):
    """Artifact output (write side). `enabled=false` is the unilateral rollback switch."""

    enabled: bool = False
    node_name: str = ""  # "" -> socket.gethostname() at runtime
    provider: ArtifactProviderSettings = ArtifactProviderSettings()
    index: IndexSettings = IndexSettings()
    ttl_max_seconds: int = 30 * 86400  # X-Haruki-Cache-TTL cap; Cloud clamps to the same value
    upload_timeout_seconds: float = 8.0  # total per request; must stay below debug._WATCHDOG_WARN_SECONDS
    upload_io_timeout_seconds: float = 4.0
    upload_retries: int = 1
    upload_concurrency: int = 4
    hash_in_pool_min_bytes: int = 262_144


class Settings(BaseSettings):
    """Main settings class."""

    model_config = SettingsConfigDict(
        env_prefix="HARUKI_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        # from_yaml passes configs.yaml as init kwargs; by default init > env, which would
        # let a key written in the yaml silently defeat HARUKI_* overrides — exactly when an
        # operator needs env to win (e.g. flipping a Skia gate during an incident). Env first.
        return (env_settings, init_settings, dotenv_settings, file_secret_settings)

    assets: AssetsSettings = AssetsSettings()
    font: FontSettings = FontSettings()
    server: ServerSettings = ServerSettings()
    logging: LoggingSettings = LoggingSettings()
    drawing: DrawingSettings = DrawingSettings()
    storage: StorageSettings = StorageSettings()

    @model_validator(mode="after")
    def fill_custom_profile_defaults(self) -> "Settings":
        custom_profile_assets = self.assets.base_dir / "asset" / "{region}-assets" / "startapp" / "custom_profile"
        if self.drawing.custom_profile_assets_dir is None:
            self.drawing.custom_profile_assets_dir = custom_profile_assets
        if self.drawing.custom_profile_fonts_dir is None:
            self.drawing.custom_profile_fonts_dir = custom_profile_assets / "font"
        if self.drawing.custom_profile_tmp_font_metadata is None:
            self.drawing.custom_profile_tmp_font_metadata = (
                self.assets.base_dir / "custom_profile" / "tmp-font-assets" / "{region}" / "metadata.json"
            )
        if self.drawing.custom_profile_shape_sprite_dir is None:
            self.drawing.custom_profile_shape_sprite_dir = custom_profile_assets / "shape"
        if self.drawing.custom_profile_unity_ui_sprite_dir is None:
            self.drawing.custom_profile_unity_ui_sprite_dir = (
                self.assets.base_dir / self.assets.result_asset_path / "customprofile"
            )
        return self

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
        if "storage" in data:
            mapped["storage"] = _strip_yaml_index_dsn(data["storage"])

        # Drawing settings
        drawing: dict = {}
        if "default_thread_pool_size" in data:
            drawing["thread_pool_size"] = data["default_thread_pool_size"]
        if "drawing" in data:
            drawing.update(data["drawing"])
        if drawing:
            mapped["drawing"] = drawing

        return cls(**mapped)


def _strip_yaml_index_dsn(storage: Any) -> Any:
    """The index DSN carries a password and configs.docker.yaml is a plaintext bind mount: env only."""
    if not isinstance(storage, dict) or not isinstance(storage.get("index"), dict):
        return storage
    if "dsn" not in storage["index"]:
        return storage
    logger.warning("settings.index_dsn_ignored source=yaml")
    index = {k: v for k, v in storage["index"].items() if k != "dsn"}
    return {**storage, "index": index}


# Singleton instance
settings = Settings.from_yaml()


# ========== Convenience exports ========== #
# These allow `from src.settings import ASSETS_BASE_DIR` style imports

# Assets
ASSETS_BASE_DIR = settings.assets.base_dir
RESULT_ASSET_PATH = settings.assets.result_asset_path
TMP_PATH = settings.assets.tmp_path

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
READINESS_UNHEALTHY_CGROUP_PERCENT = settings.drawing.readiness_unhealthy_cgroup_percent
IMAGE_CACHE_SIZE = settings.drawing.image_cache_size
IMAGE_CACHE_MAX_BYTES = settings.drawing.image_cache_max_mb * 1024 * 1024
THUMB_CACHE_SIZE = settings.drawing.thumbnail_cache_size
THUMB_CACHE_MAX_BYTES = settings.drawing.thumbnail_cache_max_mb * 1024 * 1024
COMPOSED_IMAGE_CACHE_SIZE = settings.drawing.composed_image_cache_size
COMPOSED_IMAGE_CACHE_MAX_BYTES = settings.drawing.composed_image_cache_max_mb * 1024 * 1024
COMPOSED_IMAGE_CACHE_TTL_SECONDS = settings.drawing.composed_image_cache_ttl_seconds
EXPORT_IMAGE_FORMAT = settings.drawing.export_image_format
JPG_QUALITY = settings.drawing.jpg_quality
CUSTOM_PROFILE_ASSETS_DIR = settings.drawing.custom_profile_assets_dir
CUSTOM_PROFILE_FONTS_DIR = settings.drawing.custom_profile_fonts_dir
CUSTOM_PROFILE_TMP_FONT_METADATA = settings.drawing.custom_profile_tmp_font_metadata
CUSTOM_PROFILE_SHAPE_SPRITE_DIR = settings.drawing.custom_profile_shape_sprite_dir
CUSTOM_PROFILE_UNITY_UI_SPRITE_DIR = settings.drawing.custom_profile_unity_ui_sprite_dir
CUSTOM_PROFILE_PARALLEL_WORKERS = settings.drawing.custom_profile_parallel_workers
CUSTOM_PROFILE_MAX_CONCURRENT_REQUESTS = settings.drawing.custom_profile_max_concurrent_requests
CUSTOM_PROFILE_MAX_ELEMENTS = settings.drawing.custom_profile_max_elements
CUSTOM_PROFILE_MAX_SCALE = settings.drawing.custom_profile_max_scale
CUSTOM_PROFILE_MAX_TEXT_SIZE = settings.drawing.custom_profile_max_text_size
CUSTOM_PROFILE_MAX_TEXT_LENGTH = settings.drawing.custom_profile_max_text_length
CUSTOM_PROFILE_MAX_LAYER_PIXELS = settings.drawing.custom_profile_max_layer_pixels
CUSTOM_PROFILE_MAX_SCENE_BYTES = settings.drawing.custom_profile_max_scene_mb * 1024 * 1024
CUSTOM_PROFILE_GLYPH_CACHE_SIZE = settings.drawing.custom_profile_glyph_cache_size
CUSTOM_PROFILE_GLYPH_CACHE_MAX_BYTES = settings.drawing.custom_profile_glyph_cache_max_mb * 1024 * 1024
CUSTOM_PROFILE_SPRITE_CACHE_SIZE = settings.drawing.custom_profile_sprite_cache_size
CUSTOM_PROFILE_SPRITE_CACHE_MAX_BYTES = settings.drawing.custom_profile_sprite_cache_max_mb * 1024 * 1024

# Server
SERVER_HOST = settings.server.host
SERVER_PORT = settings.server.port

# Logging
LOG_FORMAT = settings.logging.format
FIELD_STYLE = settings.logging.field_styles
