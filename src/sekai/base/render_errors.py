"""Machine-readable reasons a render failed.

The HTTP layer sends the reason as the ``code`` field of the error body (next to the unchanged ``detail``
text), so a caller classifies a failure by the code instead of matching the message. A code is part of the
API: callers depend on these values, so add new ones rather than renaming.
"""

ASSET_MISSING = "asset_missing"
ASSET_BROKEN = "asset_broken"
DATA_INSUFFICIENT = "data_insufficient"
CONTENT_TOO_LARGE = "content_too_large"

RENDER_ERROR_CODES = frozenset({ASSET_MISSING, ASSET_BROKEN, DATA_INSUFFICIENT, CONTENT_TOO_LARGE})

# An exception may carry its code in this attribute; render_error_code() reads it first.
RENDER_ERROR_CODE_ATTR = "render_error_code"


class RenderContentTooLargeError(ValueError):
    """The laid-out content or canvas exceeds its limit."""

    render_error_code = CONTENT_TOO_LARGE


class RenderDataInsufficientError(ValueError):
    """The request does not carry enough data to draw the page."""

    render_error_code = DATA_INSUFFICIENT


def render_error_code(exc: BaseException) -> str | None:
    """The structured code of a render failure, or None when the failure has no known reason."""
    explicit = getattr(exc, RENDER_ERROR_CODE_ATTR, None)
    if isinstance(explicit, str) and explicit in RENDER_ERROR_CODES:
        return explicit
    if isinstance(exc, FileNotFoundError):
        return ASSET_MISSING
    if isinstance(exc, IndexError):
        # An empty series / list indexed while drawing: the request had too little data.
        return DATA_INSUFFICIENT
    if type(exc).__name__ == "UnidentifiedImageError":
        return ASSET_BROKEN
    return None
