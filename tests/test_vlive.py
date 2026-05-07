from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from PIL import Image

from src.sekai.vlive.drawer import _compose_vlive_entry_image
from src.sekai.vlive.model import VLiveBrief, VLiveCharacterItem


def test_compose_vlive_entry_image_wraps_connected_live_characters() -> None:
    now = datetime.now(timezone.utc)
    vlive = VLiveBrief(
        id=202,
        name="Connected Live",
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=2),
        current_start_at=now,
        current_end_at=now + timedelta(minutes=30),
        living=True,
        rest_count=3,
        characters=[VLiveCharacterItem(icon_path=f"character/{idx}.png") for idx in range(26)],
    )

    loaded = {
        "characters": [Image.new("RGBA", (30, 30), (255, 80, 120, 255)) for _ in range(26)],
    }

    image = asyncio.run(_compose_vlive_entry_image(vlive, loaded, now))

    assert image.width <= 724
    assert image.height > 212
