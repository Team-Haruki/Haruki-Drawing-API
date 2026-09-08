from __future__ import annotations

from datetime import UTC, datetime, timedelta

from PIL import Image
import pytest

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.plot import Canvas, TextStyle
from src.sekai.event import drawer
from src.sekai.event.model import EventBrief, EventListRequest
from src.sekai.profile.drawer import CardFullThumbnailLayers
from src.sekai.profile.model import CardFullThumbnailRequest

NOW = datetime(2026, 2, 3, 12, 0, tzinfo=UTC)


def _image(size=(12, 8)) -> Image.Image:
    return Image.new("RGBA", size, (10, 20, 30, 255))


def _card(card_id=1) -> CardFullThumbnailRequest:
    return CardFullThumbnailRequest(
        card_id=card_id,
        card_thumbnail_path="card.png",
        rare="rarity_4",
        frame_img_path="frame.png",
        attr_img_path="attr.png",
        rare_img_path="rare.png",
        train_rank=None,
    )


def _layers(card_id=1) -> CardFullThumbnailLayers:
    image = _image((30, 30))
    return CardFullThumbnailLayers(rqd=_card(card_id), base=image, rare=image)


def _event(**updates) -> EventBrief:
    data = {
        "id": 1,
        "event_name": "Event",
        "event_type": "marathon",
        "event_type_name": "Marathon",
        "start_at": NOW - timedelta(hours=1),
        "end_at": NOW + timedelta(hours=1),
    }
    data.update(updates)
    return EventBrief(**data)


async def _async_value(value):
    return value


def _styles() -> tuple[TextStyle, TextStyle]:
    return (
        TextStyle(drawer.DEFAULT_FONT, 10, (1, 2, 3)),
        TextStyle(drawer.DEFAULT_FONT, 9, (4, 5, 6)),
    )


def test_event_list_phase_color_and_cache_key_helpers_cover_lifecycle(monkeypatch) -> None:
    assert drawer._resolve_event_list_entry_phase(NOW - timedelta(hours=1), NOW + timedelta(hours=1), NOW) == "current"
    assert drawer._resolve_event_list_entry_phase(NOW - timedelta(hours=2), NOW - timedelta(hours=1), NOW) == "past"
    assert drawer._resolve_event_list_entry_phase(NOW + timedelta(hours=1), NOW + timedelta(hours=2), NOW) == "upcoming"
    assert drawer._resolve_event_list_entry_bg_color("current") == (255, 250, 220, 200)
    assert drawer._resolve_event_list_entry_bg_color("past") == (220, 220, 220, 200)
    assert drawer._resolve_event_list_entry_bg_color("upcoming") == drawer.WIDGET_BG_COLOR

    observed = {}
    monkeypatch.setattr(
        drawer, "collect_asset_signatures", lambda root, payload: observed.setdefault("asset", (root, payload)) or {}
    )
    monkeypatch.setattr(drawer, "build_rendered_image_cache_key", lambda *args, **kwargs: (args, kwargs))
    args, kwargs = drawer._build_event_list_entry_cache_key(_event(event_banner_path="banner.png"), "current")
    assert args[0] == "event_list_entry"
    assert args[1]["phase"] == "current"
    assert "asset_signatures" in kwargs


@pytest.mark.anyio
async def test_event_list_asset_preload_covers_empty_and_all_optional_assets(monkeypatch) -> None:
    async def fake_asset(_root, path):
        return f"asset:{path}"

    async def fake_layers(card):
        return f"card:{card.card_id}"

    monkeypatch.setattr(drawer, "get_asset_image_ref", fake_asset)
    monkeypatch.setattr(drawer, "get_card_full_thumbnail_layers", fake_layers)
    assert await drawer._preload_event_entry_assets(_event()) == {}
    loaded = await drawer._preload_event_entry_assets(
        _event(
            event_banner_path="banner",
            event_cards=[_card(10), _card(20)],
            event_attr_path="attr",
            event_unit_path="unit",
            event_chara_path="chara",
        )
    )
    assert loaded == {
        "banner": "asset:banner",
        "cards": ["card:10", "card:20"],
        "attr": "asset:attr",
        "unit": "asset:unit",
        "chara": "asset:chara",
    }


@pytest.mark.anyio
async def test_event_list_entry_composition_covers_placeholder_and_full_layout(monkeypatch) -> None:
    expected = _image((200, 100))

    async def fake_get_img(self, scale=1):
        assert scale == 1
        return expected

    monkeypatch.setattr(Canvas, "get_img", fake_get_img)
    style1, style2 = _styles()
    assert await drawer._compose_event_list_entry_image(_event(), {}, "upcoming", style1, style2) is expected
    full = _event(
        event_banner_path="banner",
        event_cards=[_card(1), _card(2), _card(3)],
        event_attr_path="attr",
        event_unit_path="unit",
        event_chara_path="chara",
    )
    loaded = {
        "banner": _image(),
        "cards": [_layers(1), _layers(2), _layers(3)],
        "attr": _image(),
        "unit": _image(),
        "chara": _image(),
    }
    assert await drawer._compose_event_list_entry_image(full, loaded, "current", style1, style2) is expected


@pytest.mark.anyio
async def test_event_list_entry_cache_covers_memory_disk_and_render_miss(monkeypatch) -> None:
    style1, style2 = _styles()
    event = _event()
    memory, disk, rendered = _image((1, 1)), _image((2, 2)), _image((3, 3))
    monkeypatch.setattr(drawer, "_build_event_list_entry_cache_key", lambda *_args: "key")
    monkeypatch.setattr(drawer, "get_composed_image_cached", lambda _key: memory)
    assert await drawer._get_event_list_entry_image(event, NOW, style1, style2) is memory

    writes: list[tuple] = []
    monkeypatch.setattr(drawer, "get_composed_image_cached", lambda _key: None)
    monkeypatch.setattr(drawer, "get_composed_image_disk_cached", lambda *_args: disk)
    monkeypatch.setattr(drawer, "put_composed_image_cache", lambda *args: writes.append(args))
    assert await drawer._get_event_list_entry_image(event, NOW, style1, style2) is disk

    monkeypatch.setattr(drawer, "get_composed_image_disk_cached", lambda *_args: None)
    monkeypatch.setattr(drawer, "_preload_event_entry_assets", lambda _event: _async_value({}))
    monkeypatch.setattr(drawer, "_compose_event_list_entry_image", lambda *_args: _async_value(rendered))
    monkeypatch.setattr(drawer, "put_composed_image_disk_cache", lambda *args: writes.append(args))
    assert await drawer._get_event_list_entry_image(event, NOW, style1, style2) is rendered
    assert writes[-2] == ("key", rendered)
    assert writes[-1] == (drawer._EVENT_LIST_ENTRY_CACHE_NAMESPACE, "key", rendered)


@pytest.mark.anyio
async def test_event_list_canvas_compose_and_native_routes_cover_empty_enabled_and_disabled(monkeypatch) -> None:
    request = EventListRequest(event_info=[_event()])
    monkeypatch.setattr(drawer, "request_now", lambda _timezone: NOW)
    monkeypatch.setattr(drawer, "_get_event_list_entry_image", lambda *_args: _async_value(_image()))
    assert isinstance(await drawer._build_event_list_canvas(request), Canvas)
    assert isinstance(await drawer._build_event_list_canvas(request.model_copy(update={"event_info": []})), Canvas)

    expected = _image((4, 4))

    class FakeCanvas:
        async def get_img(self):
            return expected

    fake_canvas = FakeCanvas()
    monkeypatch.setattr(drawer, "_build_event_list_canvas", lambda _request: _async_value(fake_canvas))
    assert await drawer.compose_event_list_image(request) is expected

    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: False)
    assert await drawer.try_render_event_list_payload(request) is None

    payload = EncodedImagePayload(b"x", "image/png", "x.png", 1, 1, "RGBA", 0)
    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: True)
    monkeypatch.setattr(drawer, "render_canvas_payload", lambda *_args, **_kwargs: _async_value(payload))
    assert await drawer.try_render_event_list_payload(request) is payload
