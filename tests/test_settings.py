import logging
import os

from pydantic import ValidationError
import pytest
import yaml

from src.core import debug
from src.settings import (
    PROJECT_ROOT,
    ArtifactProviderSettings,
    AssetMirrorSettings,
    DrawingSettings,
    Settings,
    StorageProviderSettings,
    StorageSettings,
)

_SECRET_KEY_ID = "AKIA-UNIT-TEST-KEY-ID"
_SECRET_ACCESS = "unit-test-secret-access-key"
_SECRET_DSN = "postgresql://haruki:unit-test-db-password@db.internal:5432/haruki"


@pytest.fixture(autouse=True)
def _clear_ambient_haruki_env(monkeypatch):
    """Settings construction reads HARUKI_* env (env > yaml by design), so ambient
    variables — e.g. CI's HARUKI_FONT__EMOJI — would leak into these tests."""
    for key in list(os.environ):
        if key.startswith("HARUKI_"):
            monkeypatch.delenv(key, raising=False)


def test_settings_from_yaml_maps_legacy_config_and_resolves_paths(tmp_path):
    config_path = tmp_path / "configs.yaml"
    config_path.write_text(
        """
assets:
  base_dir: ./custom-data
  result_asset_path: assets
  tmp_path: temp
font:
  dir: ./fonts
  default:
    default: Regular
    bold: Bold
    heavy: Heavy
    emoji: Emoji
default_thread_pool_size: 3
drawing:
  export_image_format: jpg
  jpg_quality: 77
server:
  host: 127.0.0.1
  port: 12345
""",
        encoding="utf-8",
    )

    settings = Settings.from_yaml(config_path)

    assert settings.assets.base_dir == (PROJECT_ROOT / "custom-data").resolve()
    assert settings.assets.result_asset_path == "assets"
    assert settings.assets.tmp_path == "temp"
    assert settings.font.dir == (PROJECT_ROOT / "fonts").resolve()
    assert settings.font.default == "Regular"
    assert settings.font.bold == "Bold"
    assert settings.font.heavy == "Heavy"
    assert settings.font.emoji == "Emoji"
    assert settings.drawing.thread_pool_size == 3
    assert settings.drawing.export_image_format == "jpg"
    assert settings.drawing.jpg_quality == 77
    assert settings.drawing.use_skia_plot is True
    assert (
        settings.drawing.custom_profile_assets_dir
        == (PROJECT_ROOT / "custom-data" / "asset" / "{region}-assets" / "startapp" / "custom_profile").resolve()
    )
    assert (
        settings.drawing.custom_profile_fonts_dir
        == (
            PROJECT_ROOT / "custom-data" / "asset" / "{region}-assets" / "startapp" / "custom_profile" / "font"
        ).resolve()
    )
    assert (
        settings.drawing.custom_profile_tmp_font_metadata
        == (
            PROJECT_ROOT / "custom-data" / "custom_profile" / "tmp-font-assets" / "{region}" / "metadata.json"
        ).resolve()
    )
    assert (
        settings.drawing.custom_profile_shape_sprite_dir
        == (
            PROJECT_ROOT / "custom-data" / "asset" / "{region}-assets" / "startapp" / "custom_profile" / "shape"
        ).resolve()
    )
    assert (
        settings.drawing.custom_profile_unity_ui_sprite_dir
        == (PROJECT_ROOT / "custom-data" / "assets" / "customprofile").resolve()
    )
    assert settings.server.host == "127.0.0.1"
    assert settings.server.port == 12345


def test_settings_reads_nested_environment_overrides(monkeypatch):
    monkeypatch.setenv("HARUKI_DRAWING__THREAD_POOL_SIZE", "17")
    monkeypatch.setenv("HARUKI_DRAWING__OVERLOAD_MAX_INFLIGHT_REQUESTS", "64")
    monkeypatch.setenv("HARUKI_DRAWING__READINESS_UNHEALTHY_INFLIGHT_REQUESTS", "48")
    monkeypatch.setenv("HARUKI_DRAWING__EXPORT_IMAGE_FORMAT", "jpg")
    monkeypatch.setenv("HARUKI_DRAWING__JPG_QUALITY", "91")
    monkeypatch.setenv("HARUKI_DRAWING__USE_SKIA_PLOT", "false")
    monkeypatch.setenv("HARUKI_DRAWING__CUSTOM_PROFILE_DIAGNOSTIC_DIR", "./diagnostics")

    settings = Settings()

    assert settings.drawing.thread_pool_size == 17
    assert settings.drawing.overload_max_inflight_requests == 64
    assert settings.drawing.readiness_unhealthy_inflight_requests == 48
    assert settings.drawing.export_image_format == "jpg"
    assert settings.drawing.jpg_quality == 91
    assert settings.drawing.use_skia_plot is False
    assert settings.drawing.custom_profile_diagnostic_dir == (PROJECT_ROOT / "diagnostics").resolve()


def test_environment_overrides_beat_yaml_written_keys(tmp_path, monkeypatch):
    # configs.yaml is passed as init kwargs; env must still win so operators can flip
    # Skia gates (or any drawing key) without editing files (settings_customise_sources).
    config_path = tmp_path / "configs.yaml"
    config_path.write_text(
        """
drawing:
  use_skia_plot: true
  jpg_quality: 70
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("HARUKI_DRAWING__USE_SKIA_PLOT", "false")

    settings = Settings.from_yaml(config_path)

    assert settings.drawing.use_skia_plot is False
    assert settings.drawing.jpg_quality == 70  # yaml still applies where env is silent


@pytest.mark.parametrize("quality", [0, 101])
def test_jpg_quality_is_validated(quality):
    with pytest.raises(ValidationError):
        DrawingSettings(jpg_quality=quality)


def test_storage_and_mirror_defaults_keep_todays_behaviour():
    settings = Settings()

    assert settings.assets.source == "local"
    assert settings.assets.manifest_version is None
    assert settings.assets.mirror.dir == "mirror"
    assert settings.assets.mirror.manifest_version == "v0"
    assert settings.assets.mirror.provider.scheme == "s3"
    assert settings.assets.mirror.provider.bucket == "pjsk-assets"
    assert settings.assets.mirror.provider.root == ""
    assert settings.storage.enabled is False
    assert settings.storage.node_name == ""
    assert settings.storage.index.dsn is None
    assert settings.storage.provider.scheme == "s3"
    assert settings.storage.provider.bucket == "image-cache"
    assert settings.storage.provider.root == ""
    assert settings.storage.ttl_max_seconds == 30 * 86400
    assert StorageProviderSettings().scheme == "fs"
    assert StorageProviderSettings().path_style is True


def test_upload_timeout_stays_below_request_watchdog():
    settings = Settings()

    assert settings.storage.upload_timeout_seconds < 10.0
    assert settings.storage.upload_timeout_seconds < debug._WATCHDOG_WARN_SECONDS


@pytest.mark.parametrize(
    ("payload", "field", "expected"),
    [
        ({"provider": "garage-cn09"}, "provider", "garage-cn09"),
        ({"name": "garage-cn09"}, "provider", "garage-cn09"),
        ({"scheme": "s3"}, "scheme", "s3"),
        ({"kind": "s3"}, "scheme", "s3"),
        ({"scheme": "local"}, "scheme", "fs"),
        ({"kind": "LOCAL"}, "scheme", "fs"),
        ({"scheme": ""}, "scheme", "fs"),
        ({"scheme": "memory"}, "scheme", "memory"),
        ({"base_url": "https://assets.example"}, "base_url", "https://assets.example"),
        ({"public_base_url": "https://assets.example"}, "base_url", "https://assets.example"),
        ({"prefix": "legacy"}, "prefix", "legacy"),
        ({"path_style": False}, "path_style", False),
        ({"public_read": True}, "public_read", True),
    ],
)
def test_provider_vocabulary_aliases_round_trip(payload, field, expected):
    assert getattr(StorageProviderSettings.model_validate(payload), field) == expected


def test_provider_credential_aliases_are_secret():
    for key_id, secret in (("access_key_id", "secret_access_key"), ("access_key", "secret_key")):
        provider = StorageProviderSettings.model_validate({key_id: _SECRET_KEY_ID, secret: _SECRET_ACCESS})

        assert provider.access_key_id.get_secret_value() == _SECRET_KEY_ID
        assert provider.secret_access_key.get_secret_value() == _SECRET_ACCESS
        assert _SECRET_KEY_ID not in repr(provider)
        assert _SECRET_ACCESS not in str(provider.model_dump())
        assert _SECRET_ACCESS not in provider.model_dump_json()


@pytest.mark.parametrize("scheme", ["gcs", 3])
def test_provider_rejects_unsupported_scheme(scheme):
    with pytest.raises(ValidationError):
        StorageProviderSettings(scheme=scheme)


def test_provider_slot_accepts_model_instances():
    provider = ArtifactProviderSettings(endpoint="http://100.64.0.9:3900")

    assert StorageSettings(provider=provider).provider.endpoint == "http://100.64.0.9:3900"


def test_provider_unknown_key_warns_and_is_ignored(caplog):
    with caplog.at_level(logging.WARNING, logger="src.settings"):
        provider = StorageProviderSettings.model_validate({"bucket": "b", "cache_control": "max-age=1"})

    assert provider.bucket == "b"
    assert not hasattr(provider, "cache_control")
    assert "settings.provider_unknown_key key=cache_control" in caplog.text


def test_provider_endpoints_list_warns_and_fills_empty_endpoint(caplog):
    with caplog.at_level(logging.WARNING, logger="src.settings"):
        filled = StorageProviderSettings.model_validate({"endpoints": ["http://a:3900", "http://b:3900"]})
        kept = StorageProviderSettings.model_validate({"endpoint": "http://c:3900", "endpoints": ["http://a:3900"]})
        empty = StorageProviderSettings.model_validate({"endpoints": []})

    assert filled.endpoint == "http://a:3900"
    assert kept.endpoint == "http://c:3900"
    assert empty.endpoint == ""
    assert caplog.text.count("settings.provider_endpoints_ignored") == 3


def test_provider_describe_never_contains_secrets():
    provider = StorageProviderSettings(
        provider="garage",
        scheme="s3",
        endpoint="http://100.64.0.9:3900",
        bucket="image-cache",
        access_key_id=_SECRET_KEY_ID,
        secret_access_key=_SECRET_ACCESS,
    )

    assert provider.describe() == {
        "provider": "garage",
        "scheme": "s3",
        "endpoint": "http://100.64.0.9:3900",
        "bucket": "image-cache",
        "root": "",
    }


@pytest.mark.parametrize("value", ["/abs/mirror", "../mirror", "mirror/../../x", "..\\mirror", "", "   "])
def test_mirror_dir_rejects_absolute_and_parent_segments(value):
    with pytest.raises(ValidationError):
        AssetMirrorSettings(dir=value)


def test_mirror_dir_accepts_relative_paths():
    assert AssetMirrorSettings(dir="cache/mirror").dir == "cache/mirror"


def test_storage_and_mirror_nested_environment(monkeypatch):
    env = {
        "HARUKI_ASSETS__SOURCE": "mirror",
        "HARUKI_ASSETS__MANIFEST_VERSION": "v7",
        "HARUKI_ASSETS__MIRROR__DIR": "mirror2",
        "HARUKI_ASSETS__MIRROR__MANIFEST_VERSION": "v3",
        "HARUKI_ASSETS__MIRROR__MANIFEST_VERSION_FILE": "/run/haruki/manifest_version",
        "HARUKI_ASSETS__MIRROR__PROVIDER__ENDPOINT": "http://100.64.0.9:3900",
        "HARUKI_ASSETS__MIRROR__PROVIDER__ACCESS_KEY": _SECRET_KEY_ID,
        "HARUKI_ASSETS__MIRROR__PROVIDER__SECRET_ACCESS_KEY": _SECRET_ACCESS,
        "HARUKI_ASSETS__MIRROR__PROVIDER__PATH_STYLE": "false",
        "HARUKI_ASSETS__MIRROR__PROVIDER__OPTIONS": '{"disable_config_load": "true"}',
        "HARUKI_ASSETS__MIRROR__LOCAL_FALLBACK": "false",
        "HARUKI_ASSETS__MIRROR__FETCH_TIMEOUT_SECONDS": "2.5",
        "HARUKI_ASSETS__MIRROR__FETCH_CONCURRENCY": "3",
        "HARUKI_ASSETS__MIRROR__NEGATIVE_TTL_SECONDS": "0",
        "HARUKI_ASSETS__MIRROR__BREAKER_FAILURES": "9",
        "HARUKI_ASSETS__MIRROR__BREAKER_OPEN_SECONDS": "12.5",
        "HARUKI_ASSETS__MIRROR__MAX_BYTES": "1024",
        "HARUKI_ASSETS__MIRROR__VERSIONS_KEEP": "4",
        "HARUKI_STORAGE__ENABLED": "true",
        "HARUKI_STORAGE__NODE_NAME": "cn09",
        "HARUKI_STORAGE__PROVIDER__KIND": "memory",
        "HARUKI_STORAGE__PROVIDER__ENDPOINT": "http://100.64.0.10:3900",
        "HARUKI_STORAGE__PROVIDER__ACCESS_KEY_ID": _SECRET_KEY_ID,
        "HARUKI_STORAGE__PROVIDER__SECRET_KEY": _SECRET_ACCESS,
        "HARUKI_STORAGE__TTL_MAX_SECONDS": "3600",
        "HARUKI_STORAGE__UPLOAD_TIMEOUT_SECONDS": "6.5",
        "HARUKI_STORAGE__UPLOAD_CONCURRENCY": "2",
        "HARUKI_STORAGE__INDEX__DSN": _SECRET_DSN,
        "HARUKI_STORAGE__INDEX__POOL_MAX_SIZE": "8",
        "HARUKI_STORAGE__INDEX__CONNECT_RETRY_SECONDS": "5",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    settings = Settings()

    assets = settings.assets
    assert assets.source == "mirror"
    assert assets.manifest_version == "v7"
    assert assets.mirror.dir == "mirror2"
    assert assets.mirror.manifest_version == "v3"
    assert str(assets.mirror.manifest_version_file) == "/run/haruki/manifest_version"
    # a partial provider block keeps the slot defaults
    assert assets.mirror.provider.scheme == "s3"
    assert assets.mirror.provider.bucket == "pjsk-assets"
    assert assets.mirror.provider.endpoint == "http://100.64.0.9:3900"
    assert assets.mirror.provider.access_key_id.get_secret_value() == _SECRET_KEY_ID
    assert assets.mirror.provider.secret_access_key.get_secret_value() == _SECRET_ACCESS
    assert assets.mirror.provider.path_style is False
    assert assets.mirror.provider.options == {"disable_config_load": "true"}
    assert assets.mirror.local_fallback is False
    assert assets.mirror.fetch_timeout_seconds == 2.5
    assert assets.mirror.fetch_concurrency == 3
    assert assets.mirror.negative_ttl_seconds == 0
    assert assets.mirror.breaker_failures == 9
    assert assets.mirror.breaker_open_seconds == 12.5
    assert assets.mirror.max_bytes == 1024
    assert assets.mirror.versions_keep == 4

    storage = settings.storage
    assert storage.enabled is True
    assert storage.node_name == "cn09"
    assert storage.provider.scheme == "memory"
    assert storage.provider.bucket == "image-cache"
    assert storage.provider.endpoint == "http://100.64.0.10:3900"
    assert storage.provider.access_key_id.get_secret_value() == _SECRET_KEY_ID
    assert storage.provider.secret_access_key.get_secret_value() == _SECRET_ACCESS
    assert storage.ttl_max_seconds == 3600
    assert storage.upload_timeout_seconds == 6.5
    assert storage.upload_concurrency == 2
    assert storage.index.dsn.get_secret_value() == _SECRET_DSN
    assert storage.index.pool_max_size == 8
    assert storage.index.connect_retry_seconds == 5

    dumped = str(settings.model_dump())
    for secret in (_SECRET_KEY_ID, _SECRET_ACCESS, _SECRET_DSN):
        assert secret not in repr(settings)
        assert secret not in dumped
        assert secret not in settings.model_dump_json()


def test_from_yaml_passes_storage_through_and_drops_index_dsn(tmp_path, caplog):
    config_path = tmp_path / "configs.yaml"
    config_path.write_text(
        f"""
assets:
  source: mirror
  mirror:
    dir: mirror
    provider:
      provider: garage
      scheme: s3
      endpoint: http://100.64.0.9:3900
      root: ""
storage:
  enabled: true
  node_name: cn01
  provider:
    name: garage
    kind: local
    public_base_url: https://image-cache.example
  index:
    dsn: "{_SECRET_DSN}"
    pool_max_size: 2
""",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="src.settings"):
        settings = Settings.from_yaml(config_path)

    assert settings.assets.source == "mirror"
    assert settings.assets.mirror.provider.bucket == "pjsk-assets"
    assert settings.assets.mirror.provider.endpoint == "http://100.64.0.9:3900"
    assert settings.storage.enabled is True
    assert settings.storage.node_name == "cn01"
    assert settings.storage.provider.provider == "garage"
    assert settings.storage.provider.scheme == "fs"
    assert settings.storage.provider.base_url == "https://image-cache.example"
    assert settings.storage.index.dsn is None
    assert settings.storage.index.pool_max_size == 2
    assert "settings.index_dsn_ignored source=yaml" in caplog.text
    assert _SECRET_DSN not in caplog.text


def test_from_yaml_dsn_env_still_applies(tmp_path, monkeypatch):
    config_path = tmp_path / "configs.yaml"
    config_path.write_text("storage:\n  enabled: false\n  index: {pool_max_size: 3}\n", encoding="utf-8")
    monkeypatch.setenv("HARUKI_STORAGE__INDEX__DSN", _SECRET_DSN)

    settings = Settings.from_yaml(config_path)

    assert settings.storage.index.dsn.get_secret_value() == _SECRET_DSN
    assert settings.storage.index.pool_max_size == 3


@pytest.mark.parametrize(
    ("storage", "valid"),
    [({"enabled": False}, True), ({"index": {"pool_max_size": 1}}, True), ({"index": None}, False), ("x", False)],
)
def test_from_yaml_storage_without_dsn_logs_nothing(tmp_path, caplog, storage, valid):
    config_path = tmp_path / "configs.yaml"
    config_path.write_text(yaml.safe_dump({"storage": storage}), encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="src.settings"):
        if valid:
            assert Settings.from_yaml(config_path).storage.index.dsn is None
        else:
            with pytest.raises(ValidationError):
                Settings.from_yaml(config_path)

    assert "settings.index_dsn_ignored" not in caplog.text
