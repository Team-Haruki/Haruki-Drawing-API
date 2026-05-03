from pydantic import ValidationError
import pytest

from src.settings import PROJECT_ROOT, DrawingSettings, Settings


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
    assert settings.server.host == "127.0.0.1"
    assert settings.server.port == 12345


def test_settings_reads_nested_environment_overrides(monkeypatch):
    monkeypatch.setenv("HARUKI_DRAWING__THREAD_POOL_SIZE", "17")
    monkeypatch.setenv("HARUKI_DRAWING__EXPORT_IMAGE_FORMAT", "jpg")
    monkeypatch.setenv("HARUKI_DRAWING__JPG_QUALITY", "91")

    settings = Settings()

    assert settings.drawing.thread_pool_size == 17
    assert settings.drawing.export_image_format == "jpg"
    assert settings.drawing.jpg_quality == 91


@pytest.mark.parametrize("quality", [0, 101])
def test_jpg_quality_is_validated(quality):
    with pytest.raises(ValidationError):
        DrawingSettings(jpg_quality=quality)
