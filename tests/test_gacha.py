from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from PIL import Image

import src.sekai.gacha.drawer as gacha_drawer
from src.sekai.gacha.drawer import _paginate_gacha_list, compose_gacha_list_image
from src.sekai.gacha.model import GachaBrief, GachaFilter, GachaListRequest


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
