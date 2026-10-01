from __future__ import annotations

from io import BytesIO

from PIL import Image
import pytest

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.image_source import EncodedImageRef
from src.sekai.base.plot import Canvas, Frame, HSplit, VSplit
from src.sekai.misc import drawer
from src.sekai.misc.model import AliasListRequest


def _image(size=(20, 30), color=(10, 20, 30, 255)) -> Image.Image:
    return Image.new("RGBA", size, color)


def _encoded(image):
    stream = BytesIO()
    image.save(stream, format="PNG")
    return EncodedImageRef(stream.getvalue(), image.size, image.mode)


def _request(**updates) -> AliasListRequest:
    data = {
        "title": "Alias list",
        "entity_label": "歌曲ID",
        "entity_id": 12,
        "entity_name": "A long music name",
        "aliases": [" first ", "", "second"],
    }
    data.update(updates)
    return AliasListRequest(**data)


async def _async_value(value):
    return value


def _walk(widget):
    yield widget
    for item in getattr(widget, "items", []):
        yield from _walk(item)


def test_alias_accent_trim_path_and_image_preparation_cover_variants(monkeypatch) -> None:
    monkeypatch.setitem(drawer.CHARACTER_COLOR_CODE, 1, "#112233")
    assert drawer._with_alpha((1, 2, 3), 4) == (1, 2, 3, 4)
    assert drawer._resolve_alias_accent("角色ID", 1) == (17, 34, 51, 255)
    assert drawer._resolve_alias_accent("角色ID", 999) == drawer._ALIAS_CHARA_FALLBACK_ACCENT
    assert drawer._resolve_alias_accent("歌曲ID", 1) == drawer._ALIAS_MUSIC_ACCENT

    assert drawer._resolve_alias_trim_path(_request()) is None
    assert drawer._resolve_alias_trim_path(_request(character_trim_path=" trim.png ")) == "trim.png"
    assert (
        drawer._resolve_alias_trim_path(
            _request(character_trim_path="trim.png", character_silhouette_path=" silhouette.png ")
        )
        == "silhouette.png"
    )

    transparent_border = Image.new("RGBA", (6, 6))
    transparent_border.putpixel((3, 3), (20, 30, 40, 255))
    prepared = drawer._prepare_alias_trim_image(_encoded(transparent_border))
    assert prepared.natural_size == (1, 1)
    assert prepared.bounds == (3, 3, 4, 4)
    assert drawer._prepare_alias_trim_image(_encoded(Image.new("RGB", (2, 2), "white"))).natural_size == (2, 2)


def test_alias_flow_rows_and_width_choice_follow_measured_chips() -> None:
    aliases = ["a"] * 5
    assert drawer._alias_flow_rows(aliases, 10_000) == 1
    # A chip wider than the row is capped to the row, so every alias gets its own row and none overflows.
    assert drawer._alias_flow_rows(aliases, 1) == 5
    assert drawer._alias_flow_rows([], 500) == 0

    assert drawer._alias_width_without_trim(["短"]) == drawer._ALIAS_WIDTHS[0]
    assert drawer._alias_width_without_trim(["a very long alias text"] * 200) == drawer._ALIAS_WIDTHS[-1]


def test_alias_trim_metrics_anchor_a_large_picture_to_the_page_bottom() -> None:
    tall = drawer._prepare_alias_trim_image(_encoded(_image((100, 800))))
    wide = drawer._prepare_alias_trim_image(_encoded(_image((1200, 200))))
    hang = drawer._ALIAS_TRIM_BOTTOM_HANG

    frame_w, frame_h, display_h = drawer._resolve_alias_trim_metrics(tall, 600)
    assert display_h == 600 + drawer._ALIAS_TRIM_EXTRA_H
    assert frame_h == 600
    assert frame_w == round(display_h * 100 / 800) - drawer._ALIAS_TRIM_OVERLAP

    frame_w, frame_h, display_h = drawer._resolve_alias_trim_metrics(wide, 600)
    assert frame_w == drawer._ALIAS_TRIM_MAX_W - drawer._ALIAS_TRIM_OVERLAP
    assert display_h == round(drawer._ALIAS_TRIM_MAX_W / 6)
    assert frame_h == 600

    # A short column still gets a picture of the minimum height, and the frame keeps its head on the page.
    _, frame_h, display_h = drawer._resolve_alias_trim_metrics(tall, 100)
    assert display_h == drawer._ALIAS_TRIM_MIN_H
    assert frame_h == drawer._ALIAS_TRIM_MIN_H - hang

    # A very tall column caps the picture.
    _, frame_h, display_h = drawer._resolve_alias_trim_metrics(tall, 2000)
    assert display_h == drawer._ALIAS_TRIM_MAX_H
    assert frame_h == 2000


def test_alias_column_and_trim_panel_build_detached(monkeypatch) -> None:
    request = _request()
    aliases = ["one", "two"]
    column = drawer._build_alias_column(request, aliases, (1, 2, 3, 255), None, 700)
    assert isinstance(column, VSplit)
    assert column.parent is None
    assert len(column.items) == 2
    assert column._get_self_size()[0] == 700

    with_jacket = drawer._build_alias_column(request, aliases, (1, 2, 3, 255), _image(), 700)
    # The header row gains the jacket well in front of the title block.
    assert len(with_jacket.items[0].items[0].items) == 2
    assert len(column.items[0].items[0].items) == 2  # accent bar + title block

    tall = drawer._prepare_alias_trim_image(_encoded(_image((100, 800))))
    trim = drawer._build_alias_trim_panel(tall, 600)
    assert isinstance(trim, Frame)
    assert len(trim.items) == 1
    assert trim.parent is None
    assert trim.items[0].offset == (0, drawer._ALIAS_TRIM_BOTTOM_HANG)

    heights = iter([900, 600])
    monkeypatch.setattr(drawer, "_build_alias_column", lambda *_args, **_kwargs: Frame().set_size((10, next(heights))))
    assert drawer._resolve_alias_column_width(request, aliases, (1, 2, 3, 255), None) == (drawer._ALIAS_WIDTHS[1], 600)

    monkeypatch.setattr(drawer, "_build_alias_column", lambda *_args, **_kwargs: Frame().set_size((10, 900)))
    assert drawer._resolve_alias_column_width(request, aliases, (1, 2, 3, 255), None) == (drawer._ALIAS_WIDTHS[0], 900)


def test_alias_name_wraps_past_the_minimum_size() -> None:
    with VSplit() as box:
        drawer._draw_alias_name("short", 600)
        drawer._draw_alias_name("a" * 400, 300)
    one_line, two_lines = box.items
    assert one_line.line_count == 1
    assert two_lines.line_count == drawer._ALIAS_TITLE_LINES
    assert two_lines.w == 300
    assert two_lines.style.size == drawer._ALIAS_TITLE_MIN_SIZE


@pytest.mark.anyio
async def test_alias_canvas_builds_plain_jacket_trim_and_missing_trim_paths(monkeypatch) -> None:
    async def fake_full(_root, path, **_kwargs):
        if path == "missing.png":
            raise FileNotFoundError(path)
        image = Image.new("RGBA", (100, 200))
        image.paste((255, 255, 255, 255), (20, 10, 80, 190))
        return _encoded(image)

    monkeypatch.setattr(drawer, "get_asset_image_ref", fake_full)

    plain = await drawer._build_alias_list_canvas(_request())
    assert isinstance(plain, Canvas)
    jacket = await drawer._build_alias_list_canvas(_request(music_jacket_path="jacket.png"))
    assert isinstance(jacket, Canvas)
    missing = await drawer._build_alias_list_canvas(_request(character_trim_path="missing.png"))
    assert isinstance(missing, Canvas)
    assert not any(isinstance(item, HSplit) and len(item.items) == 2 and item.sep == 0 for item in _walk(missing))

    trimmed = await drawer._build_alias_list_canvas(_request(character_silhouette_path="trim.png"))
    rows = [item for item in _walk(trimmed) if isinstance(item, HSplit) and item.sep == 0 and len(item.items) == 2]
    assert len(rows) == 1
    column, trim_panel = rows[0].items
    assert isinstance(column, VSplit)
    assert isinstance(trim_panel, Frame)
    assert (await trimmed.get_img()).width > 0


@pytest.mark.anyio
async def test_alias_compose_and_skia_routes_cover_disabled_and_enabled(monkeypatch) -> None:
    request = _request()
    expected = _image((3, 3))

    class FakeCanvas:
        async def get_img(self):
            return expected

    monkeypatch.setattr(drawer, "_build_alias_list_canvas", lambda _request: _async_value(FakeCanvas()))
    assert await drawer.compose_alias_list_image(request) is expected

    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: False)
    assert await drawer.try_render_alias_list_payload(request) is None

    payload = EncodedImagePayload(
        image_bytes=b"png",
        media_type="image/png",
        filename="alias.png",
        image_width=1,
        image_height=1,
        image_mode="RGBA",
        encode_elapsed=0.0,
    )
    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: True)
    monkeypatch.setattr(drawer, "render_canvas_payload", lambda *_args, **_kwargs: _async_value(payload))
    assert await drawer.try_render_alias_list_payload(request) is payload
