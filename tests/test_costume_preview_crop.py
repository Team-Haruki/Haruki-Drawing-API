from PIL import Image, ImageDraw

from src.sekai.costume.drawer import (
    COSTUME_DETAIL_PREVIEW_SIZE,
    _costume_preview_cover_crop_box,
    _prepare_costume_preview_image,
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


def test_prepare_costume_preview_image_returns_exact_frame_size():
    image = Image.new("RGBA", (2800, 2000), (250, 252, 254, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((1080, 260, 1780, 1920), fill=(80, 60, 55, 255))

    prepared = _prepare_costume_preview_image(image)

    assert prepared.size == COSTUME_DETAIL_PREVIEW_SIZE
