"""HTTP errors for failed renders: the legacy ``detail`` text plus a structured ``code``.

Error bodies are ``{"detail": "<text>", "code": "<code>"}``. ``detail`` is unchanged so callers that match
the text keep working; ``code`` (one of ``src.sekai.base.render_errors.RENDER_ERROR_CODES``) is present only
when the failure has a known reason.
"""

import logging

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from src.core.debug import current_request_context
from src.sekai.base.render_errors import render_error_code
from src.sekai.skia_renderer.render_stats import record_request_failure

logger = logging.getLogger(__name__)
_LOGGED_DETAIL_LIMIT = 300


class RenderHTTPException(HTTPException):
    def __init__(self, status_code: int, detail: str, code: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


def render_http_exception(exc: BaseException, status_code: int = 500) -> HTTPException:
    """The HTTPException a route raises for ``exc``: a RenderHTTPException when the reason is known.

    Also logs the failure at WARN and counts it in ``/render-stats`` (``renders.request_failures``): most
    failures happen while the request is turned into a widget tree, before ``render_canvas_payload`` records
    an outcome, and were otherwise visible only as a bare status on ``request.end``. A failure without a known
    reason carries its traceback.
    """
    code = render_error_code(exc)
    context = current_request_context()
    record_request_failure(context["path"], code)
    detail = str(exc)
    if len(detail) > _LOGGED_DETAIL_LIMIT:
        detail = detail[:_LOGGED_DETAIL_LIMIT] + "..."
    logger.warning(
        "render.failed id=%s path=%s stage=%s status=%s code=%s error_type=%s detail=%s",
        context["request_id"],
        context["path"],
        context["stage"],
        status_code,
        code or "-",
        type(exc).__name__,
        detail,
        exc_info=exc if code is None else None,
    )
    if code is None:
        return HTTPException(status_code=status_code, detail=str(exc))
    return RenderHTTPException(status_code, str(exc), code)


async def render_http_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RenderHTTPException)
    return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status_code, headers=exc.headers)
