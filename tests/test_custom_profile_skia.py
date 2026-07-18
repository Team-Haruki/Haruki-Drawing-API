"""Pins the Skia path for /profile/custom-profile-card.

The custom profile card is a hand-built IR scene (an argued exemption in
test_route_render_contract.py): ``try_render_custom_profile_card_payload`` must follow the honor
doctrine — fail-open, exactly one /render-stats outcome per attempt, never raise — and the route
must fall back to the Pillow compose (preserving its canonical ValueError -> 400) whenever the
Skia path declines.

The unit tests fake everything native. Only the final end-to-end test needs the built extension
(IR_CAPABILITY >= 8, the Transform node) plus the real parity payload, and skips when either is
missing so CI without fixtures stays green.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
import json
from pathlib import Path

from fastapi import HTTPException
from PIL import Image
import pytest

try:
    import haruki_skia_renderer as _native
except ImportError:  # pragma: no cover - extension not built
    _native = None

from src.core.heavy_render_pool import EncodedImagePayload
import src.core.pjsk.profile as route_mod
import src.sekai.profile.custom_profile.skia as skia_mod
from src.sekai.profile.custom_profile.skia import CUSTOM_PROFILE_ENDPOINT, try_render_custom_profile_card_payload
from src.sekai.profile.model import CustomProfileCardRenderRequest
from src.sekai.skia_renderer.render_stats import get_render_stats, reset_render_stats

REPO_ROOT = Path(__file__).resolve().parent.parent
PAYLOAD_FILE = REPO_ROOT / "out" / "parity-payloads" / "custom_profile_card.json"


@pytest.fixture(autouse=True)
def _clean_stats():
    reset_render_stats()
    yield
    reset_render_stats()


def _request() -> CustomProfileCardRenderRequest:
    """Minimal VALID model (region defaults to cn); the render itself is stubbed in unit tests."""
    return CustomProfileCardRenderRequest(card={"customProfileCard": {}})


def _endpoint_stats() -> dict[str, int]:
    return get_render_stats()["endpoints"][CUSTOM_PROFILE_ENDPOINT]


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", (8, 8), (0, 128, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


# ------------------------- fail-open outcomes (no native needed) -------------------------


def test_missing_native_extension_records_fallback(monkeypatch):
    """ImportError (missing wheel OR IR_CAPABILITY < 8) -> None + exactly one fallback."""
    monkeypatch.setattr(skia_mod, "skia_plot_enabled", lambda: True)

    def _no_wheel():
        raise ImportError("haruki_skia_renderer not built")

    monkeypatch.setattr(skia_mod, "load_native_renderer", _no_wheel)

    assert asyncio.run(try_render_custom_profile_card_payload(_request())) is None
    stats = _endpoint_stats()
    assert stats["fallback"] == 1
    assert stats["total"] == 1


def test_disabled_gate_records_disabled_without_loading_native(monkeypatch):
    monkeypatch.setattr(skia_mod, "skia_plot_enabled", lambda: False)

    def _boom():  # pragma: no cover - must not run
        raise AssertionError("load_native_renderer must not run when the gate is off")

    monkeypatch.setattr(skia_mod, "load_native_renderer", _boom)

    assert asyncio.run(try_render_custom_profile_card_payload(_request())) is None
    stats = _endpoint_stats()
    assert stats["disabled"] == 1
    assert stats["total"] == 1


def test_pool_render_exception_is_contained_and_recorded(monkeypatch):
    """FAIL-OPEN: nothing escaping the pool render may propagate — the route depends on None to
    reach the Pillow compose that raises the canonical user-visible error.

    _build_scene is stubbed to raise, but with the minimal request the real PNGRenderer
    construction may fail first (missing region asset dirs on CI). Either way the whole pool task
    is inside the one broad try, so the contract is the same: return None, record exactly one
    error, and never hand the stubbed native renderer a scene.
    """
    monkeypatch.setattr(skia_mod, "skia_plot_enabled", lambda: True)

    class _Native:
        called = False

        def render_scene(self, *args, **kwargs):  # pragma: no cover - must not run
            _Native.called = True
            raise AssertionError("render_scene must not run when the scene build fails")

    monkeypatch.setattr(skia_mod, "load_native_renderer", lambda: _Native())

    def _explode(renderer, card):
        raise RuntimeError("scene assembly exploded")

    monkeypatch.setattr(skia_mod, "_build_scene", _explode)

    assert asyncio.run(try_render_custom_profile_card_payload(_request())) is None
    stats = _endpoint_stats()
    assert stats["error"] == 1
    assert stats["total"] == 1
    assert not _Native.called


# ------------------------------- the route contract -------------------------------


def test_route_serves_the_skia_payload_without_composing(monkeypatch):
    payload = EncodedImagePayload(
        image_bytes=_png_bytes(),
        media_type="image/png",
        filename="image.png",
        image_width=8,
        image_height=8,
        image_mode="RGBA",
        encode_elapsed=0.0,
    )

    async def fake_try_render(request):
        return payload

    async def _must_not_compose(request):  # pragma: no cover - must not run
        raise AssertionError("compose must not run when Skia produced a payload")

    monkeypatch.setattr(route_mod, "try_render_custom_profile_card_payload", fake_try_render)
    monkeypatch.setattr(route_mod, "compose_custom_profile_card_image", _must_not_compose)

    response = asyncio.run(route_mod.custom_profile_card(_request()))
    assert response.media_type == "image/png"
    assert response.body == payload.image_bytes


def test_route_falls_back_to_pillow_compose(monkeypatch):
    async def fake_try_render(request):
        return None  # Skia declined

    async def fake_compose(request):
        return Image.new("RGBA", (8, 8), (255, 0, 0, 128))

    monkeypatch.setattr(route_mod, "try_render_custom_profile_card_payload", fake_try_render)
    monkeypatch.setattr(route_mod, "compose_custom_profile_card_image", fake_compose)

    response = asyncio.run(route_mod.custom_profile_card(_request()))
    # The route pins PNG regardless of the global EXPORT_IMAGE_FORMAT (the card has transparency).
    assert response.media_type == "image/png"
    assert Image.open(BytesIO(response.body)).format == "PNG"


def test_route_preserves_the_value_error_400(monkeypatch):
    """try_render never raises, so an unrenderable card must still reach the Pillow compose and
    surface its canonical ValueError as a 400."""

    async def fake_try_render(request):
        return None

    async def fake_compose(request):
        raise ValueError("bad card")

    monkeypatch.setattr(route_mod, "try_render_custom_profile_card_payload", fake_try_render)
    monkeypatch.setattr(route_mod, "compose_custom_profile_card_image", fake_compose)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(route_mod.custom_profile_card(_request()))
    assert excinfo.value.status_code == 400
    assert "bad card" in excinfo.value.detail


# ------------------------------- native end-to-end -------------------------------


@pytest.mark.skipif(
    _native is None or getattr(_native, "IR_CAPABILITY", 0) < 8,
    reason="haruki_skia_renderer not built at IR_CAPABILITY >= 8 (needs the Transform node)",
)
@pytest.mark.skipif(not PAYLOAD_FILE.is_file(), reason="out/parity-payloads fixture not present")
def test_native_end_to_end_renders_the_real_payload(monkeypatch):
    from src.settings import settings

    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)
    request = CustomProfileCardRenderRequest.model_validate(json.loads(PAYLOAD_FILE.read_text(encoding="utf-8")))

    payload = asyncio.run(try_render_custom_profile_card_payload(request))
    assert payload is not None, "the real parity payload must render via Skia, not fall back"
    assert payload.media_type == "image/png"
    assert payload.backend == "skia"

    image = Image.open(BytesIO(payload.image_bytes))
    assert image.size == (2048, 909)  # PROFILE_RENDER_VIEW_W x PROFILE_RENDER_VIEW_H
    assert image.mode == "RGBA"

    stats = _endpoint_stats()
    assert stats["skia"] == 1
    assert stats["total"] == 1
