from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image, ImageFont
import pytest

from src.sekai.base import draw
from src.sekai.base.plot import Canvas, Frame


def text_length(_font: object, text: str) -> tuple[int, int]:
    return len(text), 1


def test_roundrect_background_defaults_and_overrides() -> None:
    default = draw.roundrect_bg()
    assert default.fill == draw.WIDGET_BG_COLOR
    assert default.radius == draw.WIDGET_BG_RADIUS
    assert default.blur_glass is True
    assert default.blur_glass_kwargs == {}

    overridden = draw.roundrect_bg((1, 2, 3, 4), 5, alpha=9, blur_glass=False, blur_glass_kwargs={"blur": 2})
    assert overridden.fill == (1, 2, 3, 9)
    assert overridden.radius == 5
    assert overridden.blur_glass is False
    assert overridden.blur_glass_kwargs == {"blur": 2}


def test_wrap_watermark_text_prefers_segments_then_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(draw, "get_text_size", text_length)

    assert draw.wrap_watermark_text("alpha  beta", object(), 12) == ["alpha  beta"]
    assert draw.wrap_watermark_text("alpha  beta", object(), 6) == ["alpha", "beta"]
    assert draw.wrap_watermark_text("abcdefgh", object(), 3) == ["abc", "def", "gh"]
    assert draw.wrap_watermark_text("\n   ", object(), 0) == ["", ""]
    assert draw.wrap_watermark_text("", object(), 5) == [""]


def test_watermark_layout_shrinks_only_when_line_limit_requires_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(draw, "get_font", lambda _name, size: size)
    monkeypatch.setattr(draw, "get_text_size", text_length)

    assert draw.build_watermark_layout("short", 10, 12, min_size=8) == (12, "short")
    assert draw.build_watermark_layout("abcdefgh", 2, 10, min_size=8, max_lines=2) == (8, "ab\ncd\nef\ngh")
    assert draw.get_watermark_render_spec("abcd", 2, 10) == (10, ["ab", "cd"], 2, 22)


@pytest.mark.parametrize(
    ("content_size", "canvas_size", "padding", "margin", "expected_width", "expected_content"),
    [
        ((80, 40), (100, 60), (10, 5), (2, 3), 80, (80, 40)),
        ((0, 0), (100, 60), (10, 5), (2, 3), 76, (76, 44)),
        ((0, 0), (None, None), (10, 5), (2, 3), 1, (1, 1)),
    ],
)
def test_watermark_canvas_dimensions(
    content_size: tuple[int, int],
    canvas_size: tuple[int | None, int | None],
    padding: tuple[int, int],
    margin: tuple[int, int],
    expected_width: int,
    expected_content: tuple[int, int],
) -> None:
    canvas = SimpleNamespace(
        _get_content_size=lambda: content_size,
        w=canvas_size[0],
        h=canvas_size[1],
        h_padding=padding[0],
        v_padding=padding[1],
        h_margin=margin[0],
        v_margin=margin[1],
    )

    assert draw.get_watermark_layout_width(canvas) == expected_width
    assert draw.get_watermark_content_size(canvas) == expected_content


@pytest.mark.parametrize(("top_offset", "bottom_offset"), [(1, 2), (0, 0)])
def test_add_watermark_rebuilds_canvas_footer(
    top_offset: int,
    bottom_offset: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(draw, "WATERMARK_TOP_OFFSET", top_offset)
    monkeypatch.setattr(draw, "WATERMARK_BOTTOM_OFFSET", bottom_offset)
    monkeypatch.setattr(draw, "get_font", lambda _name, size: size)
    monkeypatch.setattr(draw, "get_text_size", text_length)
    canvas = Canvas(w=100, h=60)
    canvas.set_padding((10, 5))
    canvas.add_item(Frame().set_size((80, 40)))

    draw.add_watermark(canvas, "watermark", size=10)

    assert len(canvas.items) == 1
    assert canvas.w is None
    assert canvas.h is None
    root = canvas.items[0]
    assert len(root.items) == 3 + int(bottom_offset > 0)


def test_request_watermark_context_prefers_first_timestamped_item() -> None:
    empty = SimpleNamespace(timezone=None, dt=None)
    timestamped = SimpleNamespace(timezone="Asia/Shanghai", dt=123)

    assert draw._request_watermark_context([empty, timestamped]) == ("Asia/Shanghai", 123)
    assert draw._request_watermark_context((empty,)) == (None, None)
    assert draw._request_watermark_context(timestamped) == ("Asia/Shanghai", 123)


def test_request_watermark_datetime_formatting(monkeypatch: pytest.MonkeyPatch) -> None:
    aware = datetime(2026, 8, 30, 12, 34, 56, tzinfo=ZoneInfo("Asia/Shanghai"))
    naive = aware.replace(tzinfo=None)
    monkeypatch.setattr(draw, "datetime_from_millis", lambda _value, _timezone: aware)

    assert draw._format_request_watermark_datetime(None, None) is None
    assert draw._format_request_watermark_datetime("Asia/Shanghai", 1) == "DT: 2026-08-30 12:34:56 (Asia/Shanghai)"
    assert draw._format_request_watermark_datetime("", 1) == "DT: 2026-08-30 12:34:56 (CST)"

    monkeypatch.setattr(draw, "datetime_from_millis", lambda _value, _timezone: naive)
    assert draw._format_request_watermark_datetime("", 1) == "DT: 2026-08-30 12:34:56"

    monkeypatch.setattr(draw, "datetime_from_millis", lambda _value, _timezone: None)
    monkeypatch.setattr(draw, "request_now", lambda _timezone: aware)
    assert draw._format_request_watermark_datetime(None, 0) == "DT: 2026-08-30 12:34:56 (CST)"


def test_build_request_watermark_text_and_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    request = SimpleNamespace(timezone="Asia/Shanghai", dt=1)
    monkeypatch.setattr(
        draw,
        "_format_request_watermark_datetime",
        lambda timezone, dt: None if timezone is None and dt is None else "DT: fixed",
    )

    assert draw.build_request_watermark_text(SimpleNamespace()) == draw.DEFAULT_WATERMARK
    assert draw.build_request_watermark_text(request) == f"DT: fixed  {draw.DEFAULT_WATERMARK}"
    assert draw.build_request_watermark_text(request, " suffix ") == f"DT: fixed  {draw.DEFAULT_WATERMARK}  suffix"

    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(draw, "add_watermark", lambda *args, **kwargs: calls.append((*args, kwargs)))
    canvas = object()
    draw.add_request_watermark(canvas, request, extra_suffix="suffix", size=9)
    assert calls == [(canvas, f"DT: fixed  {draw.DEFAULT_WATERMARK}  suffix", {"size": 9})]


def image_text_size(font: ImageFont.ImageFont, text: str) -> tuple[int, int]:
    if not text:
        return 0, 0
    left, top, right, bottom = font.getbbox(text)
    return right - left, bottom - top


@pytest.mark.parametrize(("mode", "height"), [("RGB", 5), ("RGBA", 100)])
def test_add_watermark_to_image_extends_footer(
    mode: str,
    height: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    font = ImageFont.load_default()
    monkeypatch.setattr(draw, "get_font", lambda _name, _size: font)
    monkeypatch.setattr(draw, "get_text_size", image_text_size)
    image = Image.new(mode, (80, height), "black")

    output = draw.add_watermark_to_image(image, "line one  line two", size=8)

    assert output.mode == "RGBA"
    assert output.width == 80
    assert output.height > height


def test_async_request_watermark_to_image_uses_thread_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    image = Image.new("RGBA", (2, 2))
    expected = Image.new("RGBA", (3, 3))
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(draw, "build_request_watermark_text", lambda *_args, **_kwargs: "built")

    async def run_in_pool(func: Any, *args: Any) -> Image.Image:
        calls.append((func, *args))
        return expected

    monkeypatch.setattr(draw, "run_in_pool", run_in_pool)
    result = asyncio.run(draw.add_request_watermark_to_image(image, object(), extra_suffix="x", size=7))

    assert result is expected
    assert calls == [(draw.add_watermark_to_image, image, "built", 7)]
