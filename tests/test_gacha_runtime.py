from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from PIL import Image
import pytest

import src.sekai.gacha.drawer as gacha_drawer
from src.sekai.gacha.model import GachaBrief, GachaDetailRequest, GachaFilter, GachaInfo, GachaListRequest, GachaWeight


def _list_request() -> GachaListRequest:
    now = datetime.now(UTC)
    return GachaListRequest(
        gachas=[
            GachaBrief(
                id=1,
                name="Gacha",
                gacha_type="normal",
                start_at=now - timedelta(days=1),
                end_at=now + timedelta(days=1),
                asset_name="gacha",
            )
        ],
        filter=GachaFilter(page=1),
    )


def _detail_request() -> GachaDetailRequest:
    return GachaDetailRequest(
        gacha=GachaInfo(
            id=1,
            name="Gacha",
            gacha_type="normal",
            start_at=1_800_000_000_000,
            end_at=1_800_086_400_000,
            asset_name="gacha",
            behaviors=[],
        ),
        weight_info=GachaWeight(),
        region="jp",
    )


def test_unknown_fallback_prefers_requested_placeholder_then_builtin(monkeypatch):
    requested = Image.new("RGBA", (5, 6), "red")
    builtin = Image.new("RGBA", (7, 8), "blue")
    calls: list[tuple[str, str | None]] = []

    async def fake_load(_base, path, on_missing=None):
        calls.append((path, on_missing))
        if path == "requested.png":
            return requested
        return builtin

    monkeypatch.setattr(gacha_drawer, "get_img_from_path", fake_load)
    assert asyncio.run(gacha_drawer.get_unknown_fallback_image("requested.png")) is requested
    assert calls == [("requested.png", "placeholder")]

    calls.clear()

    async def fail_requested(_base, path, on_missing=None):
        calls.append((path, on_missing))
        if path == "requested.png":
            raise OSError("missing")
        return builtin

    monkeypatch.setattr(gacha_drawer, "get_img_from_path", fail_requested)
    assert asyncio.run(gacha_drawer.get_unknown_fallback_image("requested.png")) is builtin
    assert calls[0] == ("requested.png", "placeholder")


def test_unknown_fallback_synthesizes_image_when_both_loads_fail(monkeypatch):
    async def fail(*_args, **_kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(gacha_drawer, "get_img_from_path", fail)
    result = asyncio.run(gacha_drawer.get_unknown_fallback_image("requested.png"))
    assert result.mode == "RGBA"
    assert result.size == (256, 256)
    assert result.getpixel((0, 0)) == (220, 220, 220, 255)


def test_eager_and_lazy_image_helpers_cover_success_failure_and_empty(monkeypatch):
    image = Image.new("RGBA", (3, 4), "green")
    fallback = Image.new("RGBA", (5, 6), "gray")

    async def eager(_base, path):
        if path == "broken.png":
            raise ValueError("broken")
        return image

    async def lazy(_base, path, **_kwargs):
        if path == "broken.png":
            raise OSError("broken")
        return f"ref:{path}"

    async def unknown(path=None):
        return (fallback, path)

    monkeypatch.setattr(gacha_drawer, "get_img_from_path", eager)
    monkeypatch.setattr(gacha_drawer, "get_asset_image_ref", lazy)
    monkeypatch.setattr(gacha_drawer, "get_unknown_fallback_image", unknown)

    assert asyncio.run(gacha_drawer.get_gacha_image_or_unknown("ok.png")) is image
    assert asyncio.run(gacha_drawer.get_gacha_image_or_unknown("broken.png")) == (fallback, "broken.png")
    assert asyncio.run(gacha_drawer.get_gacha_image_or_unknown(None, allow_empty=True)) is None
    assert asyncio.run(gacha_drawer.get_gacha_image_or_unknown(None)) == (fallback, None)

    assert asyncio.run(gacha_drawer.get_gacha_image_ref_or_unknown("ok.png")) == "ref:ok.png"
    assert asyncio.run(gacha_drawer.get_gacha_image_ref_or_unknown("broken.png")) == (fallback, "broken.png")
    assert asyncio.run(gacha_drawer.get_gacha_image_ref_or_unknown(None, allow_empty=True)) is None
    assert asyncio.run(gacha_drawer.get_gacha_image_ref_or_unknown(None)) == (fallback, None)


@pytest.mark.parametrize(
    ("logo", "banner", "failures", "expected_kind"),
    [
        ("logo.png", "banner.png", set(), "logo"),
        ("logo.png", "banner.png", {"logo.png"}, "banner"),
        ("logo.png", "banner.png", {"logo.png", "banner.png"}, "unknown"),
        (None, None, set(), "unknown"),
    ],
)
def test_list_image_fallback_chain(monkeypatch, logo, banner, failures, expected_kind):
    unknown = Image.new("RGBA", (2, 2), "gray")

    async def fake_ref(_base, path, **_kwargs):
        if path in failures:
            raise FileNotFoundError(path)
        return f"ref:{path}"

    async def fake_unknown(path=None):
        assert path is None
        return unknown

    monkeypatch.setattr(gacha_drawer, "get_asset_image_ref", fake_ref)
    monkeypatch.setattr(gacha_drawer, "get_unknown_fallback_image", fake_unknown)
    result, kind = asyncio.run(gacha_drawer.get_gacha_list_image_with_fallback(logo, banner))
    assert kind == expected_kind
    if kind == "unknown":
        assert result is unknown
    else:
        assert result == f"ref:{logo if kind == 'logo' else banner}"


def test_rarity_image_repeats_star_and_handles_missing(monkeypatch):
    star = Image.new("RGBA", (2, 3), "yellow")
    calls: list[str | None] = []

    async def fake_image(path, **_kwargs):
        calls.append(path)
        return star

    async def fake_concat(images, direction):
        assert direction == "h"
        return len(images)

    monkeypatch.setattr(gacha_drawer, "get_gacha_image_or_unknown", fake_image)
    monkeypatch.setattr(gacha_drawer, "concat_images", fake_concat)
    assert asyncio.run(gacha_drawer.get_rarity_img("rarity_3", "star.png")) == 3
    assert asyncio.run(gacha_drawer.get_rarity_img("rarity_birthday", birthday_img_path="birthday.png")) == 1
    assert calls == ["star.png", "birthday.png"]

    async def missing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(gacha_drawer, "get_gacha_image_or_unknown", missing)
    assert asyncio.run(gacha_drawer.get_rarity_img("rarity_2")) is None


def test_list_preloader_handles_empty_and_preserves_ids(monkeypatch):
    request = _list_request()
    assert asyncio.run(gacha_drawer._preload_gacha_list_images(request, [])) == {}

    async def fake_fallback(logo, banner):
        return f"{logo}:{banner}", "logo"

    request.gacha_logos = {1: "logo.png"}
    request.gacha_banners = {1: "banner.png"}
    monkeypatch.setattr(gacha_drawer, "get_gacha_list_image_with_fallback", fake_fallback)
    assert asyncio.run(gacha_drawer._preload_gacha_list_images(request, request.gachas)) == {
        1: ("logo.png:banner.png", "logo")
    }


def test_detail_background_covers_default_image_and_failed_optional(monkeypatch):
    request = _detail_request()
    assert asyncio.run(gacha_drawer._gacha_detail_background(request)) == gacha_drawer.SEKAI_BLUE_BG

    request.bg_img_path = "bg.png"

    async def ref(path):
        assert path == "bg.png"
        return Image.new("RGBA", (4, 4), "blue")

    monkeypatch.setattr(gacha_drawer, "get_gacha_image_ref_or_unknown", ref)
    background = asyncio.run(gacha_drawer._gacha_detail_background(request))
    assert isinstance(background, gacha_drawer.ImageBg)

    async def empty(_path):
        return None

    monkeypatch.setattr(gacha_drawer, "get_gacha_image_ref_or_unknown", empty)
    assert asyncio.run(gacha_drawer._gacha_detail_background(request)) == gacha_drawer.SEKAI_BLUE_BG


def test_render_payload_wrappers_cover_disabled_and_enabled(monkeypatch):
    list_request = _list_request()
    detail_request = _detail_request()
    canvas = SimpleNamespace(get_img=lambda: None)
    payload = object()

    async def fake_list(_request):
        return canvas

    async def fake_detail(_request):
        return canvas

    async def fake_render(value, *, endpoint):
        assert value is canvas
        assert endpoint in {"gacha_list", "gacha_detail"}
        return payload

    monkeypatch.setattr(gacha_drawer, "_build_gacha_list_canvas", fake_list)
    monkeypatch.setattr(gacha_drawer, "_build_gacha_detail_canvas", fake_detail)
    monkeypatch.setattr(gacha_drawer, "render_canvas_payload", fake_render)
    monkeypatch.setattr(gacha_drawer, "skia_plot_enabled", lambda: False)
    assert asyncio.run(gacha_drawer.try_render_gacha_list_payload(list_request)) is None
    assert asyncio.run(gacha_drawer.try_render_gacha_detail_payload(detail_request)) is None

    monkeypatch.setattr(gacha_drawer, "skia_plot_enabled", lambda: True)
    assert asyncio.run(gacha_drawer.try_render_gacha_list_payload(list_request)) is payload
    assert asyncio.run(gacha_drawer.try_render_gacha_detail_payload(detail_request)) is payload
