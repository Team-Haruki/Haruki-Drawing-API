"""Legacy metadata adapter, imported only when the native metadata API is unavailable."""

from io import BytesIO
from pathlib import Path

from PIL import Image

from src.core.pillow_telemetry import PILLOW_TOUCH_IMAGE_HEADER_PROBE, record_pillow_touch


def probe_asset(path: Path) -> tuple[tuple[int, int], str]:
    record_pillow_touch(PILLOW_TOUCH_IMAGE_HEADER_PROBE)
    with Image.open(path) as image:
        return image.size, image.mode


def probe_encoded(data: bytes) -> tuple[tuple[int, int], str]:
    record_pillow_touch(PILLOW_TOUCH_IMAGE_HEADER_PROBE)
    with Image.open(BytesIO(data)) as image:
        return image.size, image.mode


def probe_alpha_bounds(source) -> tuple[int, int, int, int] | None:
    from .utils import resolve_image_source_sync

    record_pillow_touch("pillow_alpha_bounds")
    return resolve_image_source_sync(source).convert("RGBA").getbbox()


def probe_foreground_bounds(image, *, detect_width: int = 700) -> tuple[int, int, int, int] | None:
    from .utils import resolve_image_source_sync

    record_pillow_touch("pillow_foreground_bounds")
    source = resolve_image_source_sync(image).convert("RGBA")
    alpha_bbox = source.getchannel("A").getbbox()
    if alpha_bbox and alpha_bbox != (0, 0, source.width, source.height):
        return alpha_bbox

    scale = min(1.0, detect_width / source.width)
    small_size = (max(1, round(source.width * scale)), max(1, round(source.height * scale)))
    sample = source.resize(small_size, Image.Resampling.BILINEAR) if small_size != source.size else source
    pixels = sample.load()
    width, height = sample.size
    edge_width = min(width, max(2, round(width * 0.02)))
    threshold = 36
    min_col_hits = max(2, round(height * 0.015))
    min_row_hits = max(2, round(width * 0.015))
    col_hits = [0] * width
    row_hits = [0] * height

    for y in range(height):
        edge_pixels = []
        for x in range(edge_width):
            edge_pixels.append(pixels[x, y])
            edge_pixels.append(pixels[width - 1 - x, y])
        bg_r = sum(item[0] for item in edge_pixels) // len(edge_pixels)
        bg_g = sum(item[1] for item in edge_pixels) // len(edge_pixels)
        bg_b = sum(item[2] for item in edge_pixels) // len(edge_pixels)

        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a > 16 and max(abs(r - bg_r), abs(g - bg_g), abs(b - bg_b)) > threshold:
                col_hits[x] += 1
                row_hits[y] += 1

    xs = [idx for idx, hits in enumerate(col_hits) if hits >= min_col_hits]
    ys = [idx for idx, hits in enumerate(row_hits) if hits >= min_row_hits]
    if not xs or not ys:
        return None

    inv_scale = 1.0 / scale
    return (
        max(0, round(min(xs) * inv_scale)),
        max(0, round(min(ys) * inv_scale)),
        min(source.width, round((max(xs) + 1) * inv_scale)),
        min(source.height, round((max(ys) + 1) * inv_scale)),
    )
