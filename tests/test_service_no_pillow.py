"""Service lifecycle must not force Pillow into otherwise native requests."""

import os
import time

import pytest

from scripts.skia_service_no_pillow import route_for_case, run_service_check
from src.sekai.base.painter_cache import cleanup_painter_disk_cache


def test_service_startup_request_and_shutdown_without_pillow(real_fonts):
    pytest.importorskip("haruki_skia_renderer")
    result = run_service_check(timeout=60)
    assert result["status"] == "ok", result
    assert result["guarded_processes"] >= 2  # service and spawned pool processes
    assert result["render_processes"] >= 1
    assert result["requests"][0]["counts"]["native_pure"] == 1


def test_service_rejects_missing_native_font_before_any_pillow_attempt(real_fonts, monkeypatch):
    pytest.importorskip("haruki_skia_renderer")
    monkeypatch.setenv("HARUKI_FONT__DEFAULT", "retirement-test-missing-font")
    result = run_service_check(timeout=60)
    assert result["status"] == "blocked", result
    assert not result.get("violations"), result
    assert "text fonts cannot be resolved" in result["error"], result


def test_custom_profile_service_metadata_path_without_pillow(real_fonts, monkeypatch, tmp_path):
    pytest.importorskip("haruki_skia_renderer")
    for key in ("ASSETS_DIR", "FONTS_DIR", "SHAPE_SPRITE_DIR", "UNITY_UI_SPRITE_DIR"):
        monkeypatch.setenv("HARUKI_DRAWING__CUSTOM_PROFILE_" + key, str(tmp_path))
    monkeypatch.setenv("HARUKI_DRAWING__CUSTOM_PROFILE_TMP_FONT_METADATA", str(tmp_path / "absent.json"))
    result = run_service_check(
        [{"path": "/api/pjsk/profile/custom-profile-card", "payload": {"card": {"customProfileCard": {}}}}], timeout=60
    )
    assert result["status"] == "ok", result
    assert result["requests"][0]["size"] == [2048, 909]
    assert result["requests"][0]["counts"]["native_pure"] == 1


def test_legacy_cache_maintenance_keeps_recent_and_non_image_files(tmp_path):
    expired = tmp_path / "old.png"
    recent = tmp_path / "new.png"
    unrelated = tmp_path / "other.txt"
    for path in (expired, recent, unrelated):
        path.write_bytes(b"fixture")
    old = time.time() - 8 * 86400
    for path in (expired, unrelated):
        os.utime(path, (old, old))
    assert cleanup_painter_disk_cache(cache_dir=str(tmp_path)) == 1
    assert not expired.exists()
    assert recent.exists()
    assert unrelated.exists()


def test_service_fixture_mapping_covers_every_registered_drawing_route():
    from scripts.skia_parity_sweep import CASES
    from src.core.main import app

    schema = app.openapi()
    covered = {route_for_case(case, schema) for case in CASES}
    registered = {
        path for path, operations in schema["paths"].items() if path.startswith("/api/pjsk") and "post" in operations
    }
    assert covered == registered
