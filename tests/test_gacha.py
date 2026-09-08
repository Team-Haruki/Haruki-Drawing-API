from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from PIL import Image
import pytest

import src.sekai.gacha.drawer as gacha_drawer
from src.sekai.gacha.drawer import _paginate_gacha_list, compose_gacha_list_image
from src.sekai.gacha.model import (
    GachaBehavior,
    GachaBrief,
    GachaDetailRequest,
    GachaFilter,
    GachaInfo,
    GachaListRequest,
    GachaWeight,
)


def _detail_request(
    *,
    start_at: int = 1_800_000_000_000,
    end_at: int = 1_800_086_400_000,
    behaviors: list[GachaBehavior] | None = None,
    weight_info: GachaWeight | None = None,
) -> GachaDetailRequest:
    return GachaDetailRequest(
        gacha=GachaInfo(
            id=1001,
            name="Detail Gacha",
            gacha_type="normal",
            start_at=start_at,
            end_at=end_at,
            asset_name="detail_gacha",
            behaviors=behaviors or [],
            rarity_3_count=12,
        ),
        weight_info=weight_info or GachaWeight(),
        region="jp",
    )


def test_compose_gacha_list_image_missing_logo_stays_bounded() -> None:
    now = datetime.now(UTC)
    request = GachaListRequest(
        gachas=[
            GachaBrief(
                id=1001,
                name="Missing Logo Gacha",
                gacha_type="normal",
                start_at=now - timedelta(days=1),
                end_at=now + timedelta(days=1),
                asset_name="missing_logo_gacha",
            )
        ],
        page_size=20,
        region="jp",
        gacha_logos={1001: "missing/logo.png"},
        filter=GachaFilter(page=1),
    )

    image = asyncio.run(compose_gacha_list_image(request))

    assert image.width <= 320


def test_paginate_gacha_list_supports_local_and_pre_paginated_requests() -> None:
    now = datetime.now(UTC)
    gachas = [
        GachaBrief(
            id=index,
            name=f"Gacha {index}",
            gacha_type="normal",
            start_at=now + timedelta(days=offset),
            end_at=now + timedelta(days=offset + 1),
            asset_name=f"gacha_{index}",
        )
        for index, offset in ((1, 2), (2, 0), (3, 1))
    ]
    local_request = GachaListRequest(gachas=gachas, page_size=2, filter=GachaFilter(page=1))

    page_items, page, total_pages = _paginate_gacha_list(local_request)

    assert [gacha.id for gacha in page_items] == [2, 3]
    assert (page, total_pages) == (1, 2)

    pre_paginated = GachaListRequest(
        gachas=gachas,
        pre_paginated=True,
        current_page=None,
        total_page=3,
        filter=GachaFilter(page=1),
    )
    page_items, page, total_pages = _paginate_gacha_list(pre_paginated)

    assert [gacha.id for gacha in page_items] == [1, 2, 3]
    assert (page, total_pages) == (3, 3)


def test_build_gacha_list_canvas_covers_time_and_image_variants(monkeypatch) -> None:
    now = datetime.now(UTC)
    gachas = [
        GachaBrief(
            id=1,
            name="Active",
            gacha_type="normal",
            start_at=now - timedelta(days=1),
            end_at=now + timedelta(days=1),
            asset_name="active",
        ),
        GachaBrief(
            id=2,
            name="Expired",
            gacha_type="normal",
            start_at=now - timedelta(days=3),
            end_at=now - timedelta(days=2),
            asset_name="expired",
        ),
        GachaBrief(
            id=3,
            name="Future",
            gacha_type="normal",
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=3),
            asset_name="future",
        ),
    ]
    logo = Image.new("RGBA", (130, 60), (20, 80, 160, 255))
    banner = Image.new("RGBA", (160, 60), (160, 80, 20, 255))
    unknown = Image.new("RGBA", (130, 60), (80, 80, 80, 255))

    async def fake_preload(request, page_gachas):
        return {1: (logo, "logo"), 2: (banner, "banner")}

    async def fake_unknown(path=None):
        assert path == "future-banner.png"
        return unknown

    monkeypatch.setattr(gacha_drawer, "_preload_gacha_list_images", fake_preload)
    monkeypatch.setattr(gacha_drawer, "get_unknown_fallback_image", fake_unknown)
    request = GachaListRequest(
        gachas=gachas,
        pre_paginated=True,
        current_page=1,
        total_page=1,
        gacha_banners={3: "future-banner.png"},
        filter=GachaFilter(page=1),
    )

    canvas = asyncio.run(gacha_drawer._build_gacha_list_canvas(request))

    assert canvas is not None


@pytest.mark.parametrize(
    ("behavior", "expected"),
    [
        (GachaBehavior(type="once_a_day", spin_count=1), "每日/单抽"),
        (GachaBehavior(type="once_a_week", spin_count=10), "每周/十连"),
        (
            GachaBehavior(type="normal", spin_count=10, colorful_pass=True, execute_limit=2),
            "月卡普通/十连(限2次)",
        ),
        (GachaBehavior(type="unknown", spin_count=5), "未知"),
    ],
)
def test_gacha_behavior_label_covers_frequency_and_limits(behavior: GachaBehavior, expected: str) -> None:
    assert gacha_drawer._gacha_behavior_label(behavior) == expected


def test_group_gacha_behaviors_preserves_first_seen_order() -> None:
    behaviors = [
        GachaBehavior(type="normal", spin_count=1, cost_type="jewel", cost_quantity=100),
        GachaBehavior(type="once_a_day", spin_count=1),
        GachaBehavior(type="normal", spin_count=1, cost_type="paid_jewel", cost_quantity=50),
    ]

    grouped = gacha_drawer._group_gacha_behaviors(behaviors)

    assert list(grouped) == ["普通/单抽", "每日/单抽"]
    assert grouped["普通/单抽"] == [behaviors[0], behaviors[2]]


@pytest.mark.parametrize(
    ("rate", "guaranteed_rate", "expected"),
    [
        (0.03, 0.0, "3%"),
        (0.03, 1.0, "3% / 100% (保底)"),
    ],
)
def test_rate_text_includes_guaranteed_rate(rate: float, guaranteed_rate: float, expected: str) -> None:
    assert gacha_drawer._rate_text(rate, guaranteed_rate) == expected


def test_pickup_rate_text_scales_guaranteed_rate() -> None:
    request = SimpleNamespace(
        pickup_cards=[SimpleNamespace(rate=0.004), SimpleNamespace(rate=0.006)],
        weight_info=SimpleNamespace(rarity_4_rate=0.03, guaranteed_rates={"rarity_4": 1.0}),
    )

    assert gacha_drawer._pickup_rate_text(request) == "1% / 33.3333% (保底)"

    request.weight_info.rarity_4_rate = 0.0
    assert gacha_drawer._pickup_rate_text(request) == "1%"


def test_preload_gacha_detail_assets_deduplicates_and_isolates_failures(monkeypatch) -> None:
    behaviors = [
        GachaBehavior(type="normal", spin_count=1, cost_type="jewel", cost_icon_path="jewel.png"),
        GachaBehavior(type="normal", spin_count=10, cost_type="jewel", cost_icon_path="jewel.png"),
    ]
    request = _detail_request(behaviors=behaviors, weight_info=GachaWeight(rarity_4_rate=0.03))
    request.logo_img_path = "logo.png"
    request.banner_img_path = "banner.png"
    request.gacha.ceil_item_img_path = "ceil.png"
    calls: list[str] = []

    async def fake_image_ref(path):
        calls.append(path)
        if path == "banner.png":
            raise OSError("broken banner")
        return path

    async def fake_rarity(rarity):
        calls.append(rarity)
        return rarity

    monkeypatch.setattr(gacha_drawer, "get_gacha_image_ref_or_unknown", fake_image_ref)
    monkeypatch.setattr(gacha_drawer, "get_rarity_img", fake_rarity)

    assets = asyncio.run(gacha_drawer._preload_gacha_detail_assets(request))

    assert assets == {
        "logo": "logo.png",
        "banner": None,
        "ceil_item": "ceil.png",
        "cost_jewel.png": "jewel.png",
        "rarity_rarity_4": "rarity_4",
    }
    assert calls.count("jewel.png") == 1


@pytest.mark.parametrize(
    ("start_offset", "end_offset"),
    [(1, 2), (-1, 1), (-2, -1)],
    ids=["future", "active", "expired"],
)
def test_build_gacha_detail_canvas_covers_timing_and_cost_variants(
    monkeypatch,
    start_offset: int,
    end_offset: int,
) -> None:
    now = datetime(2027, 1, 1, tzinfo=UTC)
    request = _detail_request(
        start_at=int((now + timedelta(days=start_offset)).timestamp() * 1000),
        end_at=int((now + timedelta(days=end_offset)).timestamp() * 1000),
        behaviors=[
            GachaBehavior(type="normal", spin_count=1),
            GachaBehavior(
                type="normal",
                spin_count=10,
                cost_type="paid_jewel",
                cost_icon_path="jewel.png",
                cost_quantity=3000,
            ),
        ],
        weight_info=GachaWeight(rarity_3_rate=0.085, rarity_4_rate=0.03, guaranteed_rates={"rarity_4": 1.0}),
    )

    async def fake_background(_request):
        return gacha_drawer.SEKAI_BLUE_BG

    async def fake_assets(_request):
        return {}

    monkeypatch.setattr(gacha_drawer, "request_now", lambda _timezone: now)
    monkeypatch.setattr(gacha_drawer, "_gacha_detail_background", fake_background)
    monkeypatch.setattr(gacha_drawer, "_preload_gacha_detail_assets", fake_assets)

    canvas = asyncio.run(gacha_drawer._build_gacha_detail_canvas(request))

    assert canvas is not None
