from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from src.sekai.gacha.drawer import compose_gacha_list_image
from src.sekai.gacha.model import GachaBrief, GachaFilter, GachaListRequest


def test_compose_gacha_list_image_missing_logo_stays_bounded() -> None:
    now = datetime.now(timezone.utc)
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
