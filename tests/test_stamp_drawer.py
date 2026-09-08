from __future__ import annotations

import asyncio

from PIL import Image

import src.sekai.stamp.drawer as stamp_drawer
from src.sekai.stamp.model import StampData, StampListRequest


def test_stamp_canvas_renders_images_colors_and_page_message(monkeypatch) -> None:
    async def fake_image_loader(_base_dir, path):
        color = (30, 90, 160, 255) if path == "one.png" else (160, 90, 30, 255)
        return Image.new("RGBA", (120, 100), color)

    monkeypatch.setattr(stamp_drawer, "get_asset_image_ref", fake_image_loader)
    request = StampListRequest(
        prompt_message="Choose a stamp",
        page_message="Page 1 / 2",
        stamps=[
            StampData(id=1, image_path="one.png", text_color=(10, 20, 30, 255)),
            StampData(id=2, image_path="two.png"),
        ],
        dt=1_700_000_000_000,
    )

    canvas = asyncio.run(stamp_drawer._build_stamp_canvas(request))
    image = asyncio.run(canvas.get_img())

    assert image.width > 0
    assert image.height > 0
