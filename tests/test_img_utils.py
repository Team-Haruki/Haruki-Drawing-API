from PIL import Image
import pytest

from src.sekai.base.img_utils import (
    adjust_image_alpha_inplace,
    center_crop_by_aspect_ratio,
    mix_image_by_color,
    multiply_image_by_color,
    open_image,
)


def test_open_image_returns_loaded_detached_copy(tmp_path) -> None:
    path = tmp_path / "image.png"
    Image.new("RGBA", (2, 1), (1, 2, 3, 4)).save(path)

    opened = open_image(path, load=False)
    path.unlink()

    assert opened.mode == "RGBA"
    assert opened.getpixel((0, 0)) == (1, 2, 3, 4)


def test_multiply_image_by_color_handles_rgb_rgba_and_conversion() -> None:
    rgb = multiply_image_by_color(Image.new("RGB", (1, 1), (100, 50, 25)), (128, 255, 0))
    assert rgb.mode == "RGB"
    assert rgb.getpixel((0, 0)) == (50, 50, 0)

    rgba = multiply_image_by_color(Image.new("RGBA", (1, 1), (100, 50, 25, 128)), (255, 128, 255, 128))
    assert rgba.getpixel((0, 0)) == (100, 25, 25, 64)

    converted = multiply_image_by_color(Image.new("L", (1, 1), 200), (255, 255, 255))
    assert converted.mode == "RGBA"
    assert converted.getpixel((0, 0)) == (200, 200, 200, 255)


def test_mix_image_by_color_blends_rgb_and_preserves_alpha() -> None:
    image = Image.new("RGBA", (1, 1), (100, 50, 0, 80))
    mixed = mix_image_by_color(image, (200, 150, 100, 128))
    assert mixed.getpixel((0, 0)) == (150, 100, 50, 80)

    converted = mix_image_by_color(Image.new("L", (1, 1), 40), (80, 120, 160, 255))
    assert converted.mode == "RGBA"
    assert converted.getpixel((0, 0)) == (80, 120, 160, 255)
    with pytest.raises(AssertionError, match="4 elements"):
        mix_image_by_color(image, (1, 2, 3))


def test_adjust_image_alpha_supports_set_multiply_and_validation() -> None:
    image = Image.new("RGBA", (1, 1), (10, 20, 30, 200))
    adjust_image_alpha_inplace(image, 0.5, "set")
    assert image.getpixel((0, 0))[3] == 127
    adjust_image_alpha_inplace(image, 128, "multiply")
    assert image.getpixel((0, 0))[3] == 63
    with pytest.raises(AssertionError):
        adjust_image_alpha_inplace(image, 1.0, "bad")

    rgb = Image.new("RGB", (1, 1), (10, 20, 30))
    adjust_image_alpha_inplace(rgb, 0.5, "set")
    assert rgb.mode == "RGB"


def test_center_crop_by_aspect_ratio_covers_height_and_width_limits() -> None:
    wide = Image.new("RGB", (100, 50), "red")
    assert center_crop_by_aspect_ratio(wide, 1.0).size == (50, 50)
    tall = Image.new("L", (50, 100), 255)
    cropped = center_crop_by_aspect_ratio(tall, 2.0)
    assert cropped.mode == "RGBA"
    assert cropped.size == (50, 25)
