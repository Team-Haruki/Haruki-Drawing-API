"""W4 keystone: get_profile_card renders through the IRPainter→Skia shim.

get_profile_card is embedded by ~15 endpoints, so locking it (renders via Skia, matches
the Pillow layout) guards the whole profile-card cluster against regressions.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image
import pytest

try:
    import haruki_skia_renderer as _native
except ImportError:  # pragma: no cover - extension not built
    _native = None

from src.sekai.base.draw import BG_PADDING, SEKAI_BLUE_BG, Canvas, add_request_watermark
from src.sekai.profile.drawer import get_profile_card
from src.sekai.profile.model import BasicProfile, ProfileCardRequest, ProfileDataSource
from src.sekai.skia_renderer import canvas as canvas_mod

pytestmark = pytest.mark.skipif(_native is None, reason="haruki_skia_renderer not built")


def _request() -> ProfileCardRequest:
    return ProfileCardRequest(
        timezone="Asia/Tokyo",
        profile=BasicProfile(
            id="6323984094818319",
            region="jp",
            nickname="星雲夏希 Haruki",
            leader_image_path="static_images/skill_score_up.png",
            has_frame=False,
        ),
        data_sources=[ProfileDataSource(name="Suite数据", source="suite", update_time=1719100000000, mode="latest")],
        mysekai_level=42,
        bg_alpha=120,
    )


async def _build_canvas(rqd: ProfileCardRequest) -> Canvas:
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        await get_profile_card(rqd)
    add_request_watermark(canvas, rqd)
    return canvas


@pytest.mark.anyio
async def test_get_profile_card_renders_via_skia(monkeypatch):
    monkeypatch.setattr(canvas_mod.settings.drawing, "use_skia_plot", True)
    rqd = _request()

    payload = await canvas_mod.render_canvas_payload(await _build_canvas(rqd), bg_hour=15.5)
    assert payload is not None, "profile card must render via the Skia shim (no SkiaUnsupported)"

    skia = Image.open(BytesIO(payload.image_bytes))
    pillow = await (await _build_canvas(rqd)).get_img()
    # Layout is computed by the same widget tree, so dimensions must match exactly.
    assert skia.size == pillow.size
    assert skia.getbbox() is not None  # not blank
