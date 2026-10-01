from __future__ import annotations

from src.sekai.base import chrome
from src.sekai.base.plot import HSplit, TextBox, VSplit


def test_chip_caps_its_width_and_keeps_the_parity_bump() -> None:
    free = chrome.chip("一个非常长的别名文本用来撑宽", (0, 0, 0, 255), padding=(12, 6))
    capped = chrome.chip("一个非常长的别名文本用来撑宽", (0, 0, 0, 255), padding=(12, 6), max_w=80)
    assert free.w is None
    assert capped.w == 80
    assert capped.overflow == "shrink"
    assert free.overflow == "clip"
    assert free._get_self_size()[1] == capped._get_self_size()[1]
    # The chip grows by at most one pixel over its em box so the ink gaps above and below are equal.
    assert free._get_self_size()[1] - (chrome.CHIP_STYLE.size + 12) in (0, 1)

    stroked = chrome.chip("x", (0, 0, 0, 255), stroke=(1, 2, 3, 4), stroke_width=2)
    assert stroked.bg.stroke == (1, 2, 3, 4)
    assert stroked.bg.stroke_width == 2


def test_soft_chip_tints_with_the_colour_and_alpha() -> None:
    box = chrome.soft_chip("soft", (10, 20, 30, 255), alpha=50)
    assert box.bg.fill == (10, 20, 30, 50)
    assert box.style.color == (10, 20, 30, 255)


def test_section_header_and_panel_compose_inside_the_open_container() -> None:
    with VSplit() as root:
        with chrome.panel(300) as box:
            chrome.section_header(
                "标题",
                (1, 2, 3, 255),
                chips=[("c", (0, 0, 0, 255))],
                soft_chips=[("s", (9, 9, 9, 255))],
                captions=["cap", ""],
            )
    assert box.w == 300
    assert root.items == [box]
    header = box.items[0]
    assert isinstance(header, HSplit)
    # bar, title, chip, soft chip, one caption (the empty one is skipped)
    assert len(header.items) == 5
    assert isinstance(header.items[1], TextBox)


def test_fitted_shrinks_before_clipping() -> None:
    wide = chrome.fitted("1,234,567,890", chrome.TITLE_STYLE, 60, min_size=12)
    assert wide.w == 60
    assert wide.style.size <= chrome.TITLE_STYLE.size
    assert chrome.mix((0, 0, 0, 255), (255, 255, 255, 255), 0.5) == (128, 128, 128, 255)
    assert chrome.alpha((1, 2, 3, 255), 9) == (1, 2, 3, 9)
