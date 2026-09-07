"""Pillow imports and legacy decoder setup, loaded only by raster execution.

Shared Unity metadata, geometry and native scene construction do not import this
module. The import guard in the retirement checks also catches callers that
swallow a rejected import of this adapter.
"""

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# Preserve the legacy renderer's decoder policy; explicit custom-profile raster
# limits are enforced at the allocation/transform boundaries.
Image.MAX_IMAGE_PIXELS = None

__all__ = ["Image", "ImageChops", "ImageDraw", "ImageFilter", "ImageFont"]
