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
    assert settings.drawing.use_skia_card_list is False
    assert settings.drawing.skia_card_list_fallback_to_pillow is True
    assert settings.drawing.skia_card_list_log_visual_metrics is False
    assert settings.drawing.use_skia_card_box is False
    assert settings.drawing.skia_card_fallback_to_pillow is True
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
    monkeypatch.setenv("HARUKI_DRAWING__USE_SKIA_CARD_LIST", "true")
    monkeypatch.setenv("HARUKI_DRAWING__SKIA_CARD_LIST_FALLBACK_TO_PILLOW", "false")
    monkeypatch.setenv("HARUKI_DRAWING__SKIA_CARD_LIST_LOG_VISUAL_METRICS", "true")
    monkeypatch.setenv("HARUKI_DRAWING__USE_SKIA_CARD_BOX", "true")
    monkeypatch.setenv("HARUKI_DRAWING__SKIA_CARD_FALLBACK_TO_PILLOW", "false")

    settings = Settings()

    assert settings.drawing.thread_pool_size == 17
    assert settings.drawing.overload_max_inflight_requests == 64
    assert settings.drawing.readiness_unhealthy_inflight_requests == 48
    assert settings.drawing.export_image_format == "jpg"
    assert settings.drawing.jpg_quality == 91
    assert settings.drawing.use_skia_card_list is True
    assert settings.drawing.skia_card_list_fallback_to_pillow is False
    assert settings.drawing.skia_card_list_log_visual_metrics is True
    assert settings.drawing.use_skia_card_box is True
    assert settings.drawing.skia_card_fallback_to_pillow is False


@pytest.mark.parametrize("quality", [0, 101])
def test_jpg_quality_is_validated(quality):
    with pytest.raises(ValidationError):
        DrawingSettings(jpg_quality=quality)
