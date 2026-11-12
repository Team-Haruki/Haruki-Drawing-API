"""The profile header: the profile card on the left, the bot logo at the right edge of the page content.

The row takes the width the page's other items already give it, so the logo never widens a page: full size
when it fits, shrunk while it stays legible, dropped otherwise. The standalone info panel keeps the bare card.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

from src.sekai.base.plot import Canvas, HSplit, ImageBox, Spacer, VSplit
from src.sekai.profile import drawer
from src.sekai.profile.model import BasicProfile, ProfileCardRequest, ProfileDataSource
from src.sekai.skia_renderer import canvas as canvas_mod

LOGO = "static_images/branding/bot_neo_logo.png"
LOGO_SIZE = (324, 128)
LOGO_RGBA = (40, 80, 160, 255)


@pytest.fixture
def logo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "static_images" / "branding").mkdir(parents=True)
    Image.new("RGBA", LOGO_SIZE, LOGO_RGBA).save(tmp_path / LOGO)
    Image.new("RGBA", (128, 128), (20, 40, 60, 255)).save(tmp_path / "avatar.png")
    monkeypatch.setattr(drawer, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(canvas_mod, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(drawer, "PROFILE_HEADER_LOGO_PATH", LOGO)
    return tmp_path


def _request() -> ProfileCardRequest:
    return ProfileCardRequest(
        timezone="Asia/Shanghai",
        profile=BasicProfile(
            id="7485938033335467520", region="jp", nickname="星雲夏希", leader_image_path="avatar.png"
        ),
        data_sources=[ProfileDataSource(name="Suite数据", source="suite", update_time=1719100000000)],
        rank=388,
        mysekai_level=42,
    )


def _header() -> drawer.ProfileHeaderRow:
    header = asyncio.run(drawer.get_profile_header(_request()))
    assert isinstance(header, drawer.ProfileHeaderRow)
    return header


def _full_logo(card_h: int) -> tuple[int, int]:
    h = min(drawer._HEADER_LOGO_MAX_H, round(card_h * drawer._HEADER_LOGO_HEIGHT_RATIO))
    return round(h * LOGO_SIZE[0] / LOGO_SIZE[1]), h


def _page(header, other_w: int) -> VSplit:
    return VSplit([header, Spacer(w=other_w, h=40)]).set_content_align("lt").set_item_align("lt").set_sep(16)


def test_logo_is_right_aligned_to_the_page_at_full_size(logo_root) -> None:
    header = _header()
    _, card_h = header.card._get_self_size()
    page = _page(header, 1180)
    assert page._get_self_size()[0] == 1180  # the logo never widens the page
    row_w, row_h, logo = header._layout()
    assert (row_w, row_h) == (1180, card_h)
    assert logo == _full_logo(card_h)
    assert header.items == [header.card, header.logo_box]


def test_logo_shrinks_on_a_narrower_page_and_is_dropped_when_illegible(logo_root) -> None:
    header = _header()
    card_w, card_h = header.card._get_self_size()
    full_w, full_h = _full_logo(card_h)

    shrunk_room = full_w - 40
    _page(header, card_w + drawer._HEADER_LOGO_GAP + shrunk_room)
    _, _, logo = header._layout()
    assert logo is not None
    assert logo[0] == shrunk_room
    assert drawer._HEADER_LOGO_MIN_H <= logo[1] < full_h

    header = _header()
    tiny_room = drawer._HEADER_LOGO_MIN_H * LOGO_SIZE[0] // LOGO_SIZE[1] - 10
    page = _page(header, card_w + drawer._HEADER_LOGO_GAP + tiny_room)
    assert header._layout()[2] is None
    header._get_self_size()
    assert header.items == [header.card]
    assert page._get_self_size()[0] == card_w + drawer._HEADER_LOGO_GAP + tiny_room

    header = _header()
    _page(header, 200)  # page narrower than the card: the card sets the width, no logo
    assert header._layout() == (card_w, card_h, None)


def test_unparented_row_measures_as_the_bare_card(logo_root) -> None:
    """card box sizes its panel from the card before placing it: that width must not change."""
    header = _header()
    assert header._get_self_size() == header.card._get_self_size()


def test_fixed_width_parent_gives_the_page_width(logo_root) -> None:
    header = _header()
    HSplit([header]).set_w(1000)  # card box's profile_panel
    assert header._layout()[0] == 1000
    header = _header()
    HSplit([header, Spacer(w=600, h=10)]).set_w(1400)  # sharing a line: no page edge to align to
    assert header._layout()[2] is None


def test_info_panel_keeps_the_bare_card(logo_root) -> None:
    """The standalone info panel is the card alone on a transparent canvas: no logo, no extra width."""
    canvas = asyncio.run(drawer._build_info_panel_canvas(_request()))
    found = []

    def walk(widget) -> None:
        found.append(widget)
        for child in getattr(widget, "items", []) or []:
            walk(child)

    walk(canvas)
    assert not [w for w in found if isinstance(w, drawer.ProfileHeaderRow)]


@pytest.mark.parametrize("path", [None, "static_images/branding/absent.png"])
def test_bare_card_when_the_logo_is_off_or_absent(logo_root, monkeypatch, path) -> None:
    monkeypatch.setattr(drawer, "PROFILE_HEADER_LOGO_PATH", path)

    async def build():
        with VSplit() as page:
            card = await drawer.get_profile_header(_request())
        return page, card

    page, card = asyncio.run(build())
    assert not isinstance(card, drawer.ProfileHeaderRow)
    assert page.items == [card]


def test_header_joins_the_open_widget_context_once(logo_root) -> None:
    async def build():
        with VSplit() as page:
            header = await drawer.get_profile_header(_request())
            Spacer(w=1180, h=40)
        return page, header

    page, header = asyncio.run(build())
    assert page.items[0] is header
    assert len(page.items) == 2
    assert header.card.parent is header
    assert not [w for w in page.items if isinstance(w, ImageBox)]


@pytest.mark.parametrize("backend", ["skia", "pillow"])
def test_logo_is_drawn_at_the_right_edge_on_both_backends(logo_root, real_fonts, backend) -> None:
    async def render():
        with Canvas(bg=None).set_padding(0) as canvas:
            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(0):
                await drawer.get_profile_header(_request())
                Spacer(w=1000, h=10)
        if backend == "skia":
            pytest.importorskip("haruki_skia_renderer")
            payload = await canvas_mod.render_canvas_payload(canvas, endpoint="test_profile_header", scale=1.0)
            return Image.open(BytesIO(payload.image_bytes)).convert("RGBA")
        return (await canvas.get_img(1.0)).convert("RGBA")

    image = asyncio.run(render())
    assert image.width == 1000
    header_h = image.height - 10
    logo_w, logo_h = _full_logo(header_h)
    centre = image.getpixel((1000 - logo_w // 2, header_h // 2))
    assert centre[:3] == LOGO_RGBA[:3]
    assert centre[3] == 255
    assert image.getpixel((1000 - logo_w - 6, header_h // 2))[3] == 0  # the gap between card and logo is clear
    assert image.getpixel((999, (header_h - logo_h) // 2 - 2))[3] == 0  # centred: clear above the logo


def test_shipped_logo_keeps_its_alpha() -> None:
    path = Path(__file__).resolve().parents[1] / "data" / LOGO
    with Image.open(path) as image:
        assert image.mode == "RGBA"
        assert image.height == 128
        alpha = image.getchannel("A")
        assert alpha.getpixel((0, 0)) == 0  # transparent corners: it sits on any page background
        assert alpha.getextrema() == (0, 255)
