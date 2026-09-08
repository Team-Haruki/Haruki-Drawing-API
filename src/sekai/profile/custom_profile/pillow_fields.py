"""Legacy image replay boundaries for neutral TMP gray8 fields."""

from .gray_field import GrayField


def to_pillow(field: GrayField):
    from src.core.pillow_telemetry import PILLOW_TOUCH_CUSTOM_PROFILE_MEM_RASTER, record_pillow_touch

    from .pillow_runtime import Image

    record_pillow_touch(PILLOW_TOUCH_CUSTOM_PROFILE_MEM_RASTER)
    return Image.frombytes("L", field.size, field.pixels)


def from_pillow(image) -> GrayField:
    from src.core.pillow_telemetry import PILLOW_TOUCH_CUSTOM_PROFILE_MEM_RASTER, record_pillow_touch

    record_pillow_touch(PILLOW_TOUCH_CUSTOM_PROFILE_MEM_RASTER)
    gray = image if image.mode == "L" else image.convert("L")
    return GrayField(gray.width, gray.height, gray.tobytes())


def resize_gray_bicubic(field: GrayField, size: tuple[int, int]) -> GrayField:
    from .pillow_runtime import Image

    return from_pillow(to_pillow(field).resize(size, Image.Resampling.BICUBIC))


def transform_gray_bicubic(field: GrayField, size: tuple[int, int], inverse: tuple[float, ...]) -> GrayField:
    from .pillow_runtime import Image

    return from_pillow(
        to_pillow(field).transform(size, Image.Transform.AFFINE, inverse, Image.Resampling.BICUBIC, fillcolor=0)
    )


def basic_text_field(path, text, size, *, max_pixels):
    from src.core.pillow_telemetry import PILLOW_TOUCH_TEXT_METRIC, record_pillow_touch

    from .cache import get_render_font
    from .limits import ensure_raster_size
    from .pillow_runtime import Image, ImageDraw

    record_pillow_touch(PILLOW_TOUCH_TEXT_METRIC)
    font = get_render_font(path, size)
    bbox = font.getbbox(text)
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if not width or not height:
        return GrayField(1, 1, b"\0"), bbox
    ensure_raster_size((width, height), max_pixels=max_pixels, label="legacy text mask")
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).text((-bbox[0], -bbox[1]), text, font=font, fill=255)
    return from_pillow(mask), bbox
