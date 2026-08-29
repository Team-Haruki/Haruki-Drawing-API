"""Shared OpenAPI metadata for HTTP errors raised by drawing endpoints."""

from typing import Final

INTERNAL_SERVER_ERROR_RESPONSES: Final = {
    500: {"description": "Image generation failed."},
}

HEAVY_RENDER_ERROR_RESPONSES: Final = {
    500: {"description": "Image generation failed."},
    503: {"description": "The isolated render worker queue is unavailable."},
    504: {"description": "The isolated render worker timed out."},
}

CUSTOM_PROFILE_ERROR_RESPONSES: Final = {
    400: {"description": "The custom profile payload is invalid or exceeds a configured limit."},
    500: {"description": "Image generation failed."},
}
