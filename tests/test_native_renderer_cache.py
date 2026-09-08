import pytest

from src.sekai.skia_renderer import canvas


class _NativeRenderer:
    def __init__(self):
        self.cleared = False

    def renderer_cache_stats(self):
        return {
            "raster_cache_max_bytes": 1024,
            "raster_cache_max_entry_bytes": 256,
            "raster_cache_oversample": 1.25,
            "raster_cache_entries": 3,
            "raster_cache_bytes": 768,
            "dimension_cache_entries": 7,
            "font_fallback_count": 0,
            "font_fallback_fonts": [],
        }

    def clear_renderer_caches(self):
        self.cleared = True


def test_native_renderer_cache_stats_are_normalized(monkeypatch):
    native = _NativeRenderer()
    monkeypatch.setattr(canvas, "load_native_renderer", lambda: native)

    stats = canvas.get_native_renderer_cache_stats()

    assert stats["available"] is True
    assert stats["enabled"] is True
    assert stats["raster_cache_entries"] == 3
    assert stats["dimension_cache_entries"] == 7


def test_native_renderer_cache_stats_fail_open(monkeypatch):
    def unavailable():
        raise ImportError("extension absent")

    monkeypatch.setattr(canvas, "load_native_renderer", unavailable)

    assert canvas.get_native_renderer_cache_stats() == {
        "available": False,
        "enabled": False,
        "error": "ImportError",
    }
    assert canvas.clear_native_renderer_caches() is False


def test_native_renderer_cache_clear_calls_extension(monkeypatch):
    native = _NativeRenderer()
    monkeypatch.setattr(canvas, "load_native_renderer", lambda: native)

    assert canvas.clear_native_renderer_caches() is True
    assert native.cleared is True


@pytest.mark.parametrize(("text_bytes", "enabled"), [(0, False), (1024, True)])
def test_native_renderer_stats_include_text_cache_when_raster_is_disabled(monkeypatch, text_bytes, enabled):
    native = _NativeRenderer()
    monkeypatch.setattr(
        native,
        "renderer_cache_stats",
        lambda: {"raster_cache_max_bytes": 0, "text_mask_cache_max_bytes": text_bytes},
    )
    monkeypatch.setattr(canvas, "load_native_renderer", lambda: native)

    stats = canvas.get_native_renderer_cache_stats()

    assert stats["available"] is True
    assert stats["enabled"] is enabled
    assert stats["text_mask_cache_max_bytes"] == text_bytes
