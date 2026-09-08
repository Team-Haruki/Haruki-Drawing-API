"""Legacy raster adapter for the shared missing-asset recipe."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from src.settings import DEFAULT_BOLD_FONT, DEFAULT_HEAVY_FONT, FONT_DIR

from .placeholder import placeholder_recipe


def _load_placeholder_font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    font_dir = Path(FONT_DIR)
    for font_name in (DEFAULT_HEAVY_FONT, DEFAULT_BOLD_FONT):
        for candidate in (font_dir / font_name, font_dir / f"{font_name}.ttf", font_dir / f"{font_name}.otf"):
            if not candidate.is_file():
                continue
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    center: tuple[float, float],
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = center[0] - text_w / 2 - bbox[0]
    y = center[1] - text_h / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=fill)


def build_placeholder(variant: str) -> Image.Image:
    recipe = placeholder_recipe(variant)
    image = Image.new("RGBA", recipe.size, recipe.background)
    draw = ImageDraw.Draw(image)
    for bounds, radius, fill, outline, width in recipe.roundrects:
        draw.rounded_rectangle(bounds, radius=radius, fill=fill, outline=outline, width=width)
    for bounds, fill, width in recipe.lines:
        draw.line(bounds, fill=fill, width=width)
    for text, center, size, fill in recipe.texts:
        _draw_centered_text(draw, text, center, _load_placeholder_font(size), fill)
    return image
