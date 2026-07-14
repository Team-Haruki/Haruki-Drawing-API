"""Startup font self-check.

A missing font is not a slow render — it is a WRONG one: Pillow degrades to a 10px bitmap face and
Rust to sans-serif, silently, on every string of every image. The two layers need different answers,
and that asymmetry is what these tests pin.
"""

from __future__ import annotations

import pytest

from src.core import main as main_mod
from src.settings import settings


def test_passes_when_every_font_resolves(real_fonts):
    """The real configured fonts on this box resolve on both backends."""
    assert main_mod._check_pillow_fonts() == ([], [])
    main_mod._self_check_fonts()  # must not raise, must not disable Skia
    assert settings.drawing.use_skia_plot is True


def test_refuses_to_start_when_pillow_cannot_resolve_a_font(monkeypatch):
    """If PILLOW cannot find the font, both backends are broken — Rust would render sans-serif and
    Pillow a bitmap face — so disabling Skia would fix nothing. Fail the deploy instead of serving
    thousands of wrong images."""
    monkeypatch.setattr(main_mod, "_check_pillow_fonts", lambda: (["SourceHanSansSC-Bold"], []))

    with pytest.raises(RuntimeError, match="text fonts cannot be resolved"):
        main_mod._self_check_fonts()


def test_a_missing_emoji_font_is_loud_but_not_fatal(monkeypatch):
    """Emoji degrade; the TEXT is still correct. Refusing to start over emoji would be a
    self-inflicted outage — the service is still perfectly useful."""
    monkeypatch.setattr(main_mod, "_check_pillow_fonts", lambda: ([], ["TwemojiMozilla"]))
    monkeypatch.setattr(main_mod, "_check_native_fonts", lambda: [])
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)

    main_mod._self_check_fonts()  # must not raise

    assert settings.drawing.use_skia_plot is True  # and must not disable Skia


def test_disables_skia_when_only_the_native_renderer_cannot_resolve_a_font(monkeypatch):
    """Pillow renders correctly but Rust cannot see the face (different font dir in the image, a
    wheel built against another layout). Rust does NOT fail on a miss — it renders sans-serif — so
    the only way to keep the images correct is to serve them with Pillow."""
    monkeypatch.setattr(main_mod, "_check_pillow_fonts", lambda: ([], []))
    monkeypatch.setattr(main_mod, "_check_native_fonts", lambda: ["SourceHanSansSC-Heavy"])
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)

    main_mod._self_check_fonts()

    assert settings.drawing.use_skia_plot is False  # degraded to Pillow, still serving


def test_a_broken_native_probe_does_not_take_the_service_down(monkeypatch):
    """The self-check must not itself be a new way to fail startup."""
    monkeypatch.setattr(main_mod, "_check_pillow_fonts", lambda: ([], []))
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)

    def _explode():
        raise RuntimeError("native probe blew up")

    monkeypatch.setattr(main_mod, "_check_native_fonts", _explode)

    main_mod._self_check_fonts()  # swallowed
    assert settings.drawing.use_skia_plot is True


def test_native_probe_detects_a_font_the_extension_cannot_resolve(monkeypatch):
    """The probe itself must actually work: point the builder at a name that does not exist and the
    native renderer must report a fallback. Guards against the probe silently always returning [].
    """
    pytest.importorskip("haruki_skia_renderer")
    import src.settings as settings_mod

    monkeypatch.setattr(settings_mod, "DEFAULT_FONT", "NoSuchFontAnywhere", raising=False)
    monkeypatch.setattr(settings_mod, "DEFAULT_BOLD_FONT", "NoSuchFontAnywhere", raising=False)
    monkeypatch.setattr(settings_mod, "DEFAULT_HEAVY_FONT", "NoSuchFontAnywhere", raising=False)
    monkeypatch.setattr(settings_mod, "DEFAULT_EMOJI_FONT", "NoSuchFontAnywhere", raising=False)

    # Three, not four: emoji are deliberately not probed here — losing the native renderer over a
    # decorative face is a disproportionate trade, and Pillow degrades on it identically anyway.
    assert main_mod._check_native_fonts() == ["NoSuchFontAnywhere"] * 3
