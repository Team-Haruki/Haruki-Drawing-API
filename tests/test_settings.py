import os

from pydantic import ValidationError
import pytest

from src.settings import PROJECT_ROOT, DrawingSettings, Settings


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

    settings = Settings()

    assert settings.drawing.thread_pool_size == 17
    assert settings.drawing.overload_max_inflight_requests == 64
    assert settings.drawing.readiness_unhealthy_inflight_requests == 48
    assert settings.drawing.export_image_format == "jpg"
    assert settings.drawing.jpg_quality == 91
    assert settings.drawing.use_skia_plot is False


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
