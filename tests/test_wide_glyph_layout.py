"""Layouts that measure their text must survive a font whose glyphs render wider.

Production briefly ran a Source Han Sans build with Latin/digit advances scaled by 1.12. Pages then
failed with "Content size is too large" whenever a fixed-width container had budgeted for the old
advances: the profile card reserved 72/118 px for the rank / MySekai-level chips, which the wider
"MySekai Lv.NN" chip outgrew. The traceback surfaced in ``add_request_watermark`` only because the
watermark is the first thing that measures the finished page. Independently of the font, a colour-tagged
name too long to keep its colours left its emptied measuring box (with the long width cached) in the row.

The wider font is simulated by scaling every measured text width, so the tests need no font files.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.sekai.base import draw, plot
from src.sekai.base.plot import Canvas, Frame, TextBox
from src.sekai.profile import drawer as profile_drawer
from src.sekai.profile.model import BasicProfile, ProfileCardRequest, ProfileDataSource

LONG_TIMEZONE = "America/Argentina/Buenos_Aires"
# the widest digits the watermark timestamp can show, in the longest IANA zone name users pick
WIDE_DT = int(datetime(2026, 12, 28, 23, 58, 59, tzinfo=ZoneInfo(LONG_TIMEZONE)).timestamp() * 1000)


@pytest.fixture(params=[1.0, 1.12, 1.5], ids=lambda scale: f"x{scale}")
def wide_glyphs(request, monkeypatch: pytest.MonkeyPatch) -> float:
    """Scale every text width the layout measures, as a font with wider advances would."""
    scale = request.param
    real = plot.get_text_size

    def scaled(font, text: str) -> tuple[int, int]:
        w, h = real(font, text)
        return int(w * scale + 0.999), h

    for module in (plot, draw, profile_drawer):
        monkeypatch.setattr(module, "get_text_size", scaled)
    return scale


def _watermark_lines(canvas: Canvas) -> list[TextBox]:
    boxes: list[TextBox] = []

    def walk(widget) -> None:
        if isinstance(widget, TextBox):
            boxes.append(widget)
        for child in getattr(widget, "items", []) or []:
            walk(child)

    walk(canvas.items[0].items[-2])  # root: [top pad, page frame, footer, bottom pad]
    return boxes


@pytest.mark.parametrize("content_w", [1, 24, 96, 180, 420])
def test_request_watermark_fits_any_page_width(wide_glyphs: float, content_w: int) -> None:
    request = SimpleNamespace(timezone=LONG_TIMEZONE, dt=WIDE_DT)
    text = draw.build_request_watermark_text(request, extra_suffix="Region: EN  Event 12345")
    assert "DT: 2026-12-28 23:58 (UTC-3)" in text

    canvas = Canvas().set_padding(8)
    canvas.add_item(Frame().set_size((content_w, 30)))
    draw.add_request_watermark(canvas, request, extra_suffix="Region: EN  Event 12345")

    width, _ = canvas._get_self_size()  # raised "Content size is too large" if the footer outgrew the page
    assert width == content_w + 16
    (footer,) = _watermark_lines(canvas)
    font = footer._get_pil_font()
    for line in footer._get_lines():
        assert plot.get_text_size(font, line)[0] <= footer.w or len(line) == 1
    asyncio.run(canvas.get_img())


def _card_request(nickname: str, *, uid: str, timezone: str, rank: int, mysekai_level: int) -> ProfileCardRequest:
    return ProfileCardRequest(
        timezone=timezone,
        dt=WIDE_DT,
        profile=BasicProfile(
            id=uid,
            region="en",
            nickname=nickname,
            is_hide_uid=False,
            leader_image_path="static_images/skill_score_up.png",
            has_frame=False,
        ),
        data_sources=[
            ProfileDataSource(name="Suite数据", source="suite", update_time=WIDE_DT - 999 * 86_400_000),
            ProfileDataSource(name="Haruki Sekai API 公开数据", source="mysekai", update_time=WIDE_DT - 12 * 3_600_000),
        ],
        rank=rank,
        mysekai_level=mysekai_level,
    )


@pytest.mark.parametrize(
    "nickname",
    [
        "ab",  # short plain name: the "烤森 Lv.NN" chip, name box sized to what the chips leave
        "星雲夏希",
        "WWWWMMMMWWWWMMMMWWWW",
        "<#DAC>星<#B68>雲<#9CF>夏<#FCA>希",  # colour-tagged name that fits
        "<#F00>WWWWWWWW<#0F0>MMMMMMMMMMMM",  # colour-tagged name too long to keep its colours
    ],
)
@pytest.mark.parametrize(("rank", "mysekai_level"), [(1, 1), (388, 38), (9999, 99)])
def test_profile_card_name_row_never_outgrows_the_card(
    wide_glyphs: float, nickname: str, rank: int, mysekai_level: int
) -> None:
    rqd = _card_request(
        nickname, uid="99999999999999999999", timezone=LONG_TIMEZONE, rank=rank, mysekai_level=mysekai_level
    )
    identity = profile_drawer._build_profile_card_identity_module(rqd, rqd.data_sources)
    assert identity._get_self_size()[0] <= profile_drawer._CARD_TEXT_W
    card = asyncio.run(profile_drawer.get_profile_card(rqd))
    assert card._get_self_size()[0] <= profile_drawer._CARD_W


def test_profile_card_level_label_uses_the_caller_label() -> None:
    assert profile_drawer._profile_card_level_label(42) == "烤森 Lv.42"
    labelled = SimpleNamespace(labels={"profile.mysekai_level": "MySekai Lv.{level}"})
    assert profile_drawer._profile_card_level_label(42, labelled) == "MySekai Lv.42"


@pytest.mark.parametrize("timezone", ["Asia/Shanghai", LONG_TIMEZONE])
@pytest.mark.parametrize("uid", ["9999999999999999999", "123456"])
def test_profile_card_timezone_is_never_cut(wide_glyphs: float, timezone: str, uid: str) -> None:
    """The timezone stays whole: inline after the ID when it fits, else on its own line."""
    rqd = _card_request("星雲夏希", uid=uid, timezone=timezone, rank=388, mysekai_level=38)
    identity = profile_drawer._build_profile_card_identity_module(rqd, rqd.data_sources)
    assert identity._get_self_size()[0] <= profile_drawer._CARD_TEXT_W
    boxes = [box for row in identity.items for box in [row, *getattr(row, "items", [])] if isinstance(box, TextBox)]
    (tz,) = [box for box in boxes if timezone in box.text]
    assert "".join(tz._get_lines()) == tz.text
