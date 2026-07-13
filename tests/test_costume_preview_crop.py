import asyncio

from PIL import Image, ImageDraw

from src.sekai.base.plot import Canvas
from src.sekai.costume.drawer import (
    COSTUME_DETAIL_PREVIEW_SIZE,
    _costume_preview_cover_crop_box,
    _CostumePreviewBox,
)


def test_costume_preview_crop_removes_horizontal_background_and_keeps_target_aspect():
    image = Image.new("RGBA", (2800, 2000), (250, 252, 254, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((1080, 260, 1780, 1920), fill=(80, 60, 55, 255))

    crop_box = _costume_preview_cover_crop_box(image)

    assert crop_box[1] == 0
    assert crop_box[3] == 2000
    assert crop_box[2] - crop_box[0] == round(2000 * COSTUME_DETAIL_PREVIEW_SIZE[0] / COSTUME_DETAIL_PREVIEW_SIZE[1])
    assert crop_box[0] > 0
    assert crop_box[2] < image.width
    assert crop_box[0] <= 1080
    assert crop_box[2] >= 1780


def test_costume_preview_box_fills_exact_frame_size_with_the_cropped_foreground():
    image = Image.new("RGBA", (2800, 2000), (250, 252, 254, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((1080, 260, 1780, 1920), fill=(80, 60, 55, 255))
    crop_box = _costume_preview_cover_crop_box(image)

    async def main():
        with Canvas() as canvas:
            _CostumePreviewBox(image, crop_box)
        return await canvas.get_img()

    rendered = asyncio.run(main())

    # The crop is applied inside paste (src_rect), so the widget draws at the frame
    # size directly instead of going through a pre-composed preview image.
    assert rendered.size == COSTUME_DETAIL_PREVIEW_SIZE
    assert rendered.getpixel((COSTUME_DETAIL_PREVIEW_SIZE[0] // 2, COSTUME_DETAIL_PREVIEW_SIZE[1] // 2))[:3] == (
        80,
        60,
        55,
    )
