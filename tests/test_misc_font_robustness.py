"""The misc pages must lay out from measured text: no fixed width may overflow when the glyphs are wider.

CI's lint-test job has no fonts (Pillow's built-in face), and the operator may switch to a font build whose
Latin and digits are 12 % wider. Both are simulated here by swapping the LAYOUT font for a wrapper that
reports wider extents; a fixed width sized for the stock font would then raise ``Content size is too large``.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageFont
import pytest

from src.sekai.base import chrome, plot, text_layout
from src.sekai.base.font_metrics import get_layout_font
from src.sekai.base.image_source import EncodedImageRef
from src.sekai.misc import drawer
from src.sekai.misc.model import (
    AliasListRequest,
    BirthdayEventTime,
    CharaBirthdayCard,
    CharaBirthdayData,
    CharaBirthdayRequest,
)


class _WideFont:
    """A layout font whose horizontal extents are ``scale`` times the wrapped font's."""

    path = None  # keep it out of the shared bbox cache keyed by (path, size)

    def __init__(self, font, scale: float) -> None:
        self._font = font
        self._scale = scale
        self.size = font.size

    def getbbox(self, text, *args, **kwargs):
        x0, y0, x1, y1 = self._font.getbbox(text, *args, **kwargs)
        return (x0, y0, x0 + round((x1 - x0) * self._scale), y1)

    def getlength(self, text, *args, **kwargs):
        return self._font.getlength(text, *args, **kwargs) * self._scale

    def getmetrics(self):
        return self._font.getmetrics()


@pytest.fixture(params=["absent", "wide"])
def layout_font(request, monkeypatch):
    # One wrapper per (font, size): the bbox cache keys a path-less font by id(), so throwaway
    # wrappers whose ids get recycled would read each other's measurements.
    fonts: dict[tuple[str, int], _WideFont] = {}

    def fake(name, size):
        key = (name, size)
        if key not in fonts:
            if request.param == "absent":
                fonts[key] = _WideFont(ImageFont.load_default(size=size), 1.0)
            else:
                fonts[key] = _WideFont(get_layout_font(name, size), 1.3)
        return fonts[key]

    for module in (chrome, plot, text_layout):
        monkeypatch.setattr(module, "get_layout_font", fake)
    monkeypatch.setattr(drawer, "get_font", fake)
    return request.param


def _encoded(image: Image.Image) -> EncodedImageRef:
    stream = BytesIO()
    image.save(stream, format="PNG")
    return EncodedImageRef(stream.getvalue(), image.size, image.mode)


def _birthday_request() -> CharaBirthdayRequest:
    base = 1_784_127_600_000
    span = BirthdayEventTime(start_at=base, end_at=base + 7 * 86_400_000 - 60_000)
    characters = [
        CharaBirthdayData(cid=cid, month=1 + cid % 12, day=1 + cid, icon_path="icon.png") for cid in range(1, 27)
    ]
    return CharaBirthdayRequest(
        cid=15,
        month=7,
        day=20,
        region_name="国际服",
        days_until_birthday=123,
        color_code="#33dd99",
        sd_image_path="sd.png",
        title_image_path="title.png",
        card_image_path="card.png",
        cards=[CharaBirthdayCard(id=1000 + i, thumbnail_path="thumb.png") for i in range(12)],
        is_fifth_anniv=True,
        gacha_time=span,
        live_time=span,
        drop_time=span,
        flower_time=span,
        party_time=span,
        all_characters=characters,
        timezone="America/Argentina/Buenos_Aires",
    )


@pytest.mark.anyio
async def test_birthday_page_lays_out_with_wider_glyphs(layout_font, monkeypatch) -> None:
    async def assets(_request):
        art = Image.new("RGBA", (2520, 1440), (20, 80, 160, 255))
        sd = Image.new("RGBA", (250, 200), (160, 80, 20, 255))
        title = Image.new("RGBA", (208, 72), (80, 160, 20, 255))
        thumbs = [Image.new("RGBA", (128, 128), (160, 20, 80, 255)) for _ in range(12)]
        icons = {cid: Image.new("RGBA", (128, 128), (20, 160, 80, 255)) for cid in range(1, 27)}
        return art, sd, title, thumbs, icons, {}

    monkeypatch.setattr(drawer, "_load_chara_birthday_assets", assets)
    canvas = await drawer._build_chara_birthday_canvas(_birthday_request())
    image = await canvas.get_img()
    assert image.width > 0


@pytest.mark.anyio
@pytest.mark.parametrize("variant", ["music", "character", "plain"])
async def test_alias_page_lays_out_with_wider_glyphs(layout_font, variant, monkeypatch) -> None:
    async def asset(_root, path, **_kwargs):
        if path == "trim.png":
            image = Image.new("RGBA", (1920, 1440))
            image.paste((255, 255, 255, 255), (400, 100, 1500, 1440))
            return _encoded(image)
        return _encoded(Image.new("RGBA", (740, 740), (200, 40, 40, 255)))

    monkeypatch.setattr(drawer, "get_asset_image_ref", asset)
    aliases = ["吸血鬼", "vampire", "ヴァンパイア / DECO*27 feat. 初音ミク (2021.03.04)", "x" * 300, "🧛"] * 12
    request = AliasListRequest(
        title="歌曲别名" if variant == "music" else "角色别名",
        entity_label="歌曲ID" if variant == "music" else "角色ID",
        entity_id=213,
        entity_name="きみとぼくのうた ~ A Very Long Latin Music Title That Keeps Going On And On (Game Size)",
        music_jacket_path="jacket.png" if variant == "music" else None,
        character_trim_path="trim.png" if variant == "character" else None,
        aliases=aliases,
    )
    canvas = await drawer._build_alias_list_canvas(request)
    image = await canvas.get_img()
    assert image.width > 0
