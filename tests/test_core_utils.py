from io import BytesIO

from PIL import Image

from src.core.utils import _encode_image


def test_encode_image_defaults_to_png():
    image = Image.new("RGBA", (8, 6), (10, 20, 30, 40))

    buffer, media_type, filename = _encode_image(image, "png", 85)

    assert media_type == "image/png"
    assert filename == "image.png"
    with Image.open(buffer) as decoded:
        assert decoded.size == (8, 6)
        assert decoded.mode == "RGBA"


def test_encode_image_converts_alpha_images_for_jpeg():
    image = Image.new("RGBA", (8, 6), (10, 20, 30, 40))

    buffer, media_type, filename = _encode_image(image, "jpg", 85)

    assert media_type == "image/jpeg"
    assert filename == "image.jpg"
    assert isinstance(buffer, BytesIO)
    with Image.open(buffer) as decoded:
        assert decoded.size == (8, 6)
        assert decoded.mode == "RGB"
