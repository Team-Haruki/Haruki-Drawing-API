"""CardFullThumbnailBox draws the thumbnail natively on both backends. These pin the two
places where porting the legacy Pillow composer to Painter primitives silently drifted:
the level label's text anchor, and the alpha the old composer used to hard-reset."""

from __future__ import annotations

import asyncio

from PIL import Image, ImageDraw

from src.sekai.base.painter import DEFAULT_BOLD_FONT, get_font
from src.sekai.base.plot import Canvas
from src.sekai.profile.drawer import CardFullThumbnailBox, CardFullThumbnailLayers
from src.sekai.profile.model import CardFullThumbnailRequest

ART = 128


def _layers(**overrides) -> CardFullThumbnailLayers:
    rqd = CardFullThumbnailRequest(
        card_id=1,
        card_thumbnail_path="x.png",
        rare="rarity_4",
        frame_img_path="frame.png",
        attr_img_path="attr.png",
        rare_img_path="rare.png",
        train_rank=None,
        level=60,
        is_pcard=True,
        **overrides,
    )
    base = Image.new("RGBA", (ART, ART), (10, 120, 30, 255))  # opaque card art
    # A frame whose border is anti-aliased (alpha 128) — this is what used to eat the
    # composed thumbnail's alpha via Pillow's masked paste.
    frame = Image.new("RGBA", (ART, ART), (0, 0, 0, 0))
    ImageDraw.Draw(frame).rectangle((0, 0, ART - 1, ART - 1), outline=(255, 0, 0, 128), width=6)
    rare = Image.new("RGBA", (16, 16), (255, 255, 0, 128))
    return CardFullThumbnailLayers(rqd=rqd, base=base, rare=rare, frame=frame)


def _render(layers: CardFullThumbnailLayers, size=(ART, ART)) -> Image.Image:
    async def main():
        with Canvas(bg=None) as canvas:
            CardFullThumbnailBox(layers, size=size, image_size_mode="fill")
        return await canvas.get_img()

    return asyncio.run(main())


def test_thumbnail_stays_opaque_under_semi_transparent_overlays():
    """The legacy composer ended with img.putalpha(mask), forcing the inside of the rounded
    rect fully opaque. push_clip_roundrect only MULTIPLIES alpha, so the overlay layers have
    to be alpha_composited (paste_with_alpha_blend) or the page background and the drop
    shadow bleed through the frame/star edges as a halo."""
    img = _render(_layers()).convert("RGBA")
    alpha = img.getchannel("A")
    inner = [(x, y) for y in range(20, ART - 20) for x in range(20, ART - 20)]
    translucent = [p for p in inner if alpha.getpixel(p) != 255]
    assert not translucent, f"{len(translucent)} translucent px inside the clip, e.g. {translucent[:5]}"


def test_level_label_sits_inside_the_level_bar(real_fonts):
    """Painter.text anchors the baseline at y + ink-height('哇'); ImageDraw.text anchored the
    ascender top. The legacy y constant is in ImageDraw terms, so without converting it the
    label rides ~4px high and clips out of the bar."""
    with_label = _render(_layers()).convert("RGBA")
    without_label = _render(_layers(custom_text=" ")).convert("RGBA")

    # Isolate the label ink: the only thing that differs between the two renders.
    ink_rows = [
        y for y in range(ART) for x in range(ART) if with_label.getpixel((x, y)) != without_label.getpixel((x, y))
    ]
    assert ink_rows, "expected the level label to render"

    bar_top = ART - 24
    assert min(ink_rows) >= bar_top, f"label ink starts at row {min(ink_rows)}, above the bar top {bar_top}"
    assert max(ink_rows) <= ART - 1

    # And it matches where the legacy ImageDraw call put it (default 'la' anchor at h-31).
    legacy = Image.new("RGBA", (ART, ART), (0, 0, 0, 0))
    ImageDraw.Draw(legacy).text((6, ART - 31), "Lv.60", font=get_font(DEFAULT_BOLD_FONT, 20), fill=(255, 255, 255, 255))
    legacy_box = legacy.getbbox()
    assert (min(ink_rows), max(ink_rows)) == (legacy_box[1], legacy_box[3] - 1)
