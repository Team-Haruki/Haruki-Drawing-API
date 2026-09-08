from __future__ import annotations

from PIL import Image
import pytest

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.plot import Canvas, Frame, HSplit, TextStyle, VSplit
from src.sekai.misc import drawer
from src.sekai.misc.model import AliasListRequest


def _image(size=(20, 30), color=(10, 20, 30, 255)) -> Image.Image:
    return Image.new("RGBA", size, color)


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


def _styles():
    style = TextStyle(drawer.DEFAULT_FONT, 12, drawer.BLACK)
    return (style,) * 6


def test_alias_accent_width_trim_path_and_image_preparation_cover_variants(monkeypatch) -> None:
    monkeypatch.setitem(drawer.CHARACTER_COLOR_CODE, 1, "#112233")
    assert drawer._with_alpha((1, 2, 3), 4) == (1, 2, 3, 4)
    assert drawer._resolve_alias_accent("角色ID", 1) == (17, 34, 51, 255)
    assert drawer._resolve_alias_accent("角色ID", 999) == (255, 204, 170)
    assert drawer._resolve_alias_accent("歌曲ID", 1) == (110, 180, 255)

    assert drawer._resolve_alias_name_box_width("x", True) == 240
    assert drawer._resolve_alias_name_box_width("x", False) == 280
    assert drawer._resolve_alias_name_box_width("x" * 100, True, True, 500) == 286
    assert drawer._resolve_alias_name_box_width("x" * 100, False, True, 500) == 354

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
    prepared = drawer._prepare_alias_trim_image(transparent_border)
    assert prepared.size == (1, 1)
    assert prepared.getpixel((0, 0))[3] == 255
    assert drawer._prepare_alias_trim_image(Image.new("RGB", (2, 2), "white")).mode == "RGBA"


def test_alias_trim_metrics_and_panels_cover_wide_tall_jacket_and_plain_layouts(monkeypatch) -> None:
    tall = _image((100, 800))
    wide = _image((1200, 200))
    tall_metrics = drawer._resolve_alias_trim_metrics(tall, 700, 600)
    wide_metrics = drawer._resolve_alias_trim_metrics(wide, 700, 600)
    assert tall_metrics[1] == 600
    assert wide_metrics[0] <= drawer._ALIAS_TRIM_MAX_FRAME_W

    styles = _styles()
    request = _request()
    info_plain = drawer._build_alias_info_panel(request, (1, 2, 3), None, 2, 700, 300, *styles[:4])
    info_jacket = drawer._build_alias_info_panel(request, (1, 2, 3), _image(), 2, 700, 300, *styles[:4])
    assert len(info_plain.items[1].items) == 1
    assert len(info_jacket.items[1].items) == 2

    aliases = ["one", "two"]
    alias_panel = drawer._build_alias_list_panel(aliases, (1, 2, 3), 700, 620, styles[4], styles[3], styles[5])
    assert len(alias_panel.items[1].items) == 2
    left = drawer._build_alias_left_panel(
        request,
        aliases,
        (1, 2, 3),
        None,
        700,
        620,
        300,
        *styles,
    )
    assert isinstance(left, VSplit)
    assert len(left.items) == 2
    trim = drawer._build_alias_trim_panel(tall, (700, 600))
    assert isinstance(trim, Frame)
    assert len(trim.items) == 1

    heights = iter([900, 700])

    def fake_left(*_args, **_kwargs):
        return Frame().set_size((10, next(heights)))

    monkeypatch.setattr(drawer, "_build_alias_left_panel", fake_left)
    assert drawer._resolve_alias_panel_widths(request, aliases, (1, 2, 3), None, 760, *styles)[:2] == (780, 700)

    monkeypatch.setattr(drawer, "_build_alias_left_panel", lambda *_args, **_kwargs: Frame().set_size((10, 900)))
    assert drawer._resolve_alias_panel_widths(request, aliases, (1, 2, 3), None, 760, *styles)[0] == 700


@pytest.mark.anyio
async def test_alias_canvas_builds_plain_jacket_trim_and_missing_trim_paths(monkeypatch) -> None:
    async def fake_resized(*_args, **_kwargs):
        return _image((92, 92))

    async def fake_full(_root, path, **_kwargs):
        if path == "missing.png":
            raise FileNotFoundError(path)
        image = Image.new("RGBA", (100, 200))
        image.paste((255, 255, 255, 255), (20, 10, 80, 190))
        return image

    monkeypatch.setattr(drawer, "get_img_resized", fake_resized)
    monkeypatch.setattr(drawer, "get_img_from_path", fake_full)

    plain = await drawer._build_alias_list_canvas(_request())
    assert isinstance(plain, Canvas)
    jacket = await drawer._build_alias_list_canvas(_request(music_jacket_path="jacket.png"))
    assert isinstance(jacket, Canvas)
    missing = await drawer._build_alias_list_canvas(_request(character_trim_path="missing.png"))
    assert isinstance(missing, Canvas)

    monkeypatch.setattr(drawer, "_resolve_alias_panel_widths", lambda *_args, **_kwargs: (700, 620, 300))
    monkeypatch.setattr(drawer, "_build_alias_left_panel", lambda *_args, **_kwargs: VSplit().set_size((700, 600)))
    monkeypatch.setattr(drawer, "_build_alias_trim_panel", lambda *_args, **_kwargs: Frame().set_size((200, 600)))
    trimmed = await drawer._build_alias_list_canvas(_request(character_silhouette_path="trim.png"))
    assert any(isinstance(item, HSplit) for item in _walk(trimmed))


def _walk(widget):
    yield widget
    for item in getattr(widget, "items", []):
        yield from _walk(item)


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
