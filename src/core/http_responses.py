"""Shared OpenAPI metadata for HTTP errors raised by drawing endpoints."""

from typing import Final

_IMAGE_GENERATION_FAILED = "Image generation failed."

INTERNAL_SERVER_ERROR_RESPONSES: Final = {
    500: {"description": _IMAGE_GENERATION_FAILED},
}

HEAVY_RENDER_ERROR_RESPONSES: Final = {
    500: {"description": _IMAGE_GENERATION_FAILED},
    503: {"description": "The isolated render worker queue is unavailable."},
    504: {"description": "The isolated render worker timed out."},
}

CUSTOM_PROFILE_ERROR_RESPONSES: Final = {
    400: {"description": "The custom profile payload is invalid or exceeds a configured limit."},
    500: {"description": _IMAGE_GENERATION_FAILED},
}
