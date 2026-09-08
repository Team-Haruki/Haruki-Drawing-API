"""Startup validates the active native renderer without importing legacy font services."""

from __future__ import annotations

import pytest

from src.core import main as main_mod
from src.settings import settings


def test_passes_when_every_font_resolves(real_fonts, monkeypatch):
    pytest.importorskip("haruki_skia_renderer")
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)

    main_mod._self_check_fonts()
    assert settings.drawing.use_skia_plot is True


def test_refuses_to_start_when_native_cannot_resolve_a_font(monkeypatch):
    monkeypatch.setattr(main_mod, "_check_native_fonts", lambda: ["SourceHanSansSC-Bold"])
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)
    with pytest.raises(RuntimeError, match="text fonts cannot be resolved"):
        main_mod._self_check_fonts()


def test_a_missing_emoji_font_is_loud_but_not_fatal(monkeypatch, caplog):
    monkeypatch.setattr(main_mod, "_check_native_fonts", lambda *, emoji=False: ["MissingEmoji"] if emoji else [])
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)
    main_mod._self_check_fonts()
    assert settings.drawing.use_skia_plot is True
    assert "MissingEmoji" in caplog.text


def test_missing_native_font_cannot_activate_legacy_renderer(monkeypatch):
    monkeypatch.setattr(main_mod, "_check_native_fonts", lambda: ["SourceHanSansSC-Heavy"])
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)
    with pytest.raises(RuntimeError):
        main_mod._self_check_fonts()
    assert settings.drawing.use_skia_plot is True


def test_emoji_probe_error_does_not_disable_valid_native_text(monkeypatch, caplog):
    def probe(*, emoji=False):
        if emoji:
            raise RuntimeError("unsupported emoji face")
        return []

    monkeypatch.setattr(main_mod, "_check_native_fonts", probe)
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)
    main_mod._self_check_fonts()
    assert settings.drawing.use_skia_plot is True
    assert "unsupported emoji face" in caplog.text


@pytest.mark.parametrize("error", [ImportError("missing wheel"), RuntimeError("broken probe")])
def test_a_broken_native_probe_prevents_startup(monkeypatch, error):
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)

    def explode():
        raise error

    monkeypatch.setattr(main_mod, "_check_native_fonts", explode)
    with pytest.raises(RuntimeError):
        main_mod._self_check_fonts()
    assert settings.drawing.use_skia_plot is True


def test_disabled_native_renderer_prevents_startup(monkeypatch):
    monkeypatch.setattr(settings.drawing, "use_skia_plot", False)
    with pytest.raises(RuntimeError, match="Native rendering is required"):
        main_mod._self_check_fonts()


def test_native_probe_detects_a_font_the_extension_cannot_resolve(monkeypatch):
    pytest.importorskip("haruki_skia_renderer")
    import src.settings as settings_mod

    for key in ("DEFAULT_FONT", "DEFAULT_BOLD_FONT", "DEFAULT_HEAVY_FONT", "DEFAULT_EMOJI_FONT"):
        monkeypatch.setattr(settings_mod, key, "NoSuchFontAnywhere")
    assert main_mod._check_native_fonts() == ["NoSuchFontAnywhere"] * 3
    assert main_mod._check_native_fonts(emoji=True) == ["NoSuchFontAnywhere"]
