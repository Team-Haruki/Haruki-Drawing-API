"""All gacha inputs, including missing assets and rarity strips, stay lazy."""

import asyncio

import numpy as np
from PIL import Image, ImageDraw
import pytest

from src.sekai.base.image_source import MissingImageRef
from src.sekai.base.plot import Canvas, CanvasImageBox, ImageBox
from src.sekai.base.utils import concat_images
from src.sekai.gacha import drawer


@pytest.mark.parametrize("rarity", ["rarity_1", "rarity_2", "rarity_3", "rarity_4", "rarity_birthday"])
def test_rarity_strip_preserves_old_concat_then_resize(rarity, tmp_path, monkeypatch):
    image = Image.new("RGBA", (31, 29), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((2, 2, 28, 26), fill=(255, 200, 0, 160))
    image.save(tmp_path / "star.png")
    monkeypatch.setattr(drawer, "ASSETS_BASE_DIR", tmp_path)
    count = 1 if rarity == "rarity_birthday" else int(rarity[-1])
    expected_strip = asyncio.run(concat_images([image] * count))
    canvas = asyncio.run(drawer.get_rarity_img(rarity, "star.png", "star.png"))
    with Canvas().set_padding(0) as page:
        CanvasImageBox(canvas, size=(None, 24), sampling="pillow_bicubic")
    actual = page.get_img_sync()
    with Canvas().set_padding(0) as old_page:
        ImageBox(expected_strip, size=(None, 24))
    expected = old_page.get_img_sync()
    assert np.array_equal(np.asarray(actual), np.asarray(expected))


def test_missing_logo_banner_and_unknown_keep_lazy_placeholder(tmp_path, monkeypatch):
    monkeypatch.setattr(drawer, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(Image, "new", lambda *a, **k: pytest.fail("Pillow pixels created for missing asset"))
    image, kind = asyncio.run(drawer.get_gacha_list_image_with_fallback("missing-logo.png", "missing-banner.png"))
    assert kind == "unknown"
    assert isinstance(image, MissingImageRef)
    assert image.size == (512, 512)


def test_terminal_gacha_fallback_uses_solid_recipe(monkeypatch):
    async def broken(*args, **kwargs):
        raise OSError("asset unavailable")

    monkeypatch.setattr(drawer, "get_asset_image_ref", broken)
    monkeypatch.setattr(Image, "new", lambda *a, **k: pytest.fail("Pillow pixels created for terminal fallback"))
    image = asyncio.run(drawer.get_unknown_fallback_image("broken.png"))
    assert image == MissingImageRef("gacha_unknown")
    assert image.size == (256, 256)
