"""Pillow raster adapter for the shared gradient values."""

from PIL import Image


def gradient_image(gradient, size, mask=None):
    colors = gradient.get_colors(size)
    mode = "RGBA" if colors.shape[-1] == 4 else "RGB"
    img = Image.fromarray(colors, mode)
    if mode == "RGB":
        img = img.convert("RGBA")
    if mask:
        assert mask.size == size, "Mask size must match image size"
        if mask.mode == "RGBA":
            mask = mask.split()[3]
        else:
            mask = mask.convert("L")
        img.putalpha(mask)
    return img
