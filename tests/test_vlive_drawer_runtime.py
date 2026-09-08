from __future__ import annotations

from datetime import UTC, datetime, timedelta

from PIL import Image
import pytest

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.plot import Canvas
from src.sekai.vlive import drawer
from src.sekai.vlive.model import VLiveBrief, VLiveCharacterItem, VLiveListRequest, VLiveRewardItem

NOW = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)


def _image(size=(20, 10)):
    return Image.new("RGBA", size, (10, 20, 30, 255))


def _live(**updates) -> VLiveBrief:
    data = {
        "id": 1,
        "name": "Virtual Live",
        "start_at": NOW - timedelta(hours=1),
        "end_at": NOW + timedelta(hours=1),
        "rest_count": 2,
    }
    data.update(updates)
    return VLiveBrief(**data)


async def _async_value(value):
    return value


def test_vlive_time_status_window_and_cache_key_helpers_cover_all_states(monkeypatch) -> None:
    assert drawer._format_time(None) == "-"
    assert drawer._format_time(NOW) == "2026-01-02 12:00:00"
    assert drawer._format_relative(None, NOW) == "-"
    assert drawer._format_relative(NOW + timedelta(seconds=20), NOW) == "刚刚"
    assert drawer._format_relative(NOW + timedelta(days=2), NOW) == "2天后"
    assert drawer._format_relative(NOW - timedelta(days=2), NOW) == "2天前"
    assert drawer._format_relative(NOW + timedelta(hours=2), NOW).endswith("后")
    assert drawer._format_relative(NOW - timedelta(minutes=2), NOW).endswith("前")
    assert drawer._build_vlive_time_text("开始", None, NOW) == "开始 - (-)"

    assert drawer._build_vlive_status_text(_live(living=True), NOW) == "当前Live进行中!"
    assert "下一场" in drawer._build_vlive_status_text(_live(current_start_at=NOW + timedelta(hours=1)), NOW)
    assert drawer._build_vlive_status_text(_live(), NOW) == "已结束"
    current = _live(current_start_at=NOW, current_end_at=NOW + timedelta(minutes=1))
    assert drawer._get_display_window(current) == (current.current_start_at, current.current_end_at)
    assert drawer._get_display_window(_live()) == (NOW - timedelta(hours=1), NOW + timedelta(hours=1))

    monkeypatch.setattr(drawer, "build_rendered_image_cache_key", lambda *args, **kwargs: (args, kwargs))
    args, kwargs = drawer._build_vlive_entry_cache_key(_live(living=True), NOW)
    assert args[0] == "vlive_list_entry"
    assert kwargs["extra"] == {"state": "living", "bucket": "202601021200"}


@pytest.mark.anyio
async def test_vlive_asset_preload_and_entry_composition_cover_empty_and_full_sections(monkeypatch) -> None:
    async def fake_asset(_root, path):
        return _image((len(path) + 1, 10))

    monkeypatch.setattr(drawer, "get_asset_image_ref", fake_asset)
    assert await drawer._preload_vlive_entry_assets(_live()) == {}
    live = _live(
        banner_path="banner",
        rewards=[VLiveRewardItem(image_path="reward", quantity=0)],
        characters=[VLiveCharacterItem(icon_path="character")],
    )
    loaded = await drawer._preload_vlive_entry_assets(live)
    assert set(loaded) == {"banner", "rewards", "characters"}

    async def fake_get_img(self, scale=1):
        assert scale == 1
        return _image((100, 80))

    monkeypatch.setattr(Canvas, "get_img", fake_get_img)
    image = await drawer._compose_vlive_entry_image(live, loaded, NOW)
    assert image.size == (100, 80)
    empty = await drawer._compose_vlive_entry_image(_live(), {}, NOW)
    assert empty.size == (100, 80)


@pytest.mark.anyio
async def test_vlive_entry_cache_covers_memory_disk_and_render_miss(monkeypatch) -> None:
    live = _live()
    memory = _image((1, 1))
    disk = _image((2, 2))
    rendered = _image((3, 3))
    monkeypatch.setattr(drawer, "_build_vlive_entry_cache_key", lambda *_args: "key")
    monkeypatch.setattr(drawer, "get_composed_image_cached", lambda _key: memory)
    assert await drawer._get_vlive_list_entry_image(live, NOW) is memory

    writes: list[tuple] = []
    monkeypatch.setattr(drawer, "get_composed_image_cached", lambda _key: None)
    monkeypatch.setattr(drawer, "get_composed_image_disk_cached", lambda *_args: disk)
    monkeypatch.setattr(drawer, "put_composed_image_cache", lambda *args: writes.append(args))
    assert await drawer._get_vlive_list_entry_image(live, NOW) is disk

    monkeypatch.setattr(drawer, "get_composed_image_disk_cached", lambda *_args: None)
    monkeypatch.setattr(drawer, "_preload_vlive_entry_assets", lambda _live: _async_value({}))
    monkeypatch.setattr(drawer, "_compose_vlive_entry_image", lambda *_args: _async_value(rendered))
    monkeypatch.setattr(drawer, "put_composed_image_disk_cache", lambda *args: writes.append(args))
    assert await drawer._get_vlive_list_entry_image(live, NOW) is rendered
    assert writes[-2] == ("key", rendered)
    assert writes[-1] == (drawer._VLIVE_LIST_ENTRY_CACHE_NAMESPACE, "key", rendered)


@pytest.mark.anyio
async def test_vlive_list_canvas_compose_and_native_routes_cover_empty_enabled_and_disabled(monkeypatch) -> None:
    request = VLiveListRequest(region="jp", timezone="UTC", lives=[_live()])
    entry = _image((10, 10))
    monkeypatch.setattr(drawer, "_get_vlive_list_entry_image", lambda *_args: _async_value(entry))
    canvas = await drawer._build_vlive_list_canvas(request, NOW)
    assert isinstance(canvas, Canvas)
    assert isinstance(await drawer._build_vlive_list_canvas(request.model_copy(update={"lives": []}), NOW), Canvas)

    expected = _image((4, 4))

    class FakeCanvas:
        async def get_img(self):
            return expected

    monkeypatch.setattr(drawer, "_build_vlive_list_canvas", lambda *_args, **_kwargs: _async_value(FakeCanvas()))
    assert await drawer.compose_vlive_list_image(request) is expected
    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: False)
    assert await drawer.try_render_vlive_list_payload(request) is None

    payload = EncodedImagePayload(b"x", "image/png", "x.png", 1, 1, "RGBA", 0)
    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: True)
    monkeypatch.setattr(drawer, "request_now", lambda _timezone: NOW)
    monkeypatch.setattr(drawer, "render_canvas_payload", lambda *_args, **_kwargs: _async_value(payload))
    assert await drawer.try_render_vlive_list_payload(request) is payload
