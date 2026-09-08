import asyncio

from PIL import Image

from src.sekai.base.plot import TextBox
from src.sekai.music import drawer
from src.sekai.music.model import PlayProgressCount, PlayProgressRequest
from src.sekai.profile.model import ProfileCardRequest


def _walk_widgets(widget):
    yield widget
    for item in getattr(widget, "items", []):
        yield from _walk_widgets(item)


def test_play_progress_result_counts_keep_their_text_shadows(monkeypatch):
    async def fake_profile_card(_request):
        return None

    async def fake_asset_ref(*_args, **_kwargs):
        return Image.new("RGBA", (16, 16), (255, 255, 255, 255))

    monkeypatch.setattr(drawer, "get_profile_card", fake_profile_card)
    monkeypatch.setattr(drawer, "get_asset_image_ref", fake_asset_ref)

    request = PlayProgressRequest(
        counts=[PlayProgressCount(level=31, total=101, clear=77, fc=42, ap=13)],
        difficulty="master",
        profile=ProfileCardRequest(),
    )
    canvas = asyncio.run(drawer._build_play_progress_canvas(request))
    text_boxes = {item.text: item for item in _walk_widgets(canvas) if isinstance(item, TextBox)}

    assert text_boxes["24"].style.use_shadow is False
    assert text_boxes["35"].style.use_shadow is True
    assert text_boxes["29"].style.use_shadow is True
    assert text_boxes["13"].style.use_shadow is True
    assert text_boxes["35"].style.shadow_offset == 2
