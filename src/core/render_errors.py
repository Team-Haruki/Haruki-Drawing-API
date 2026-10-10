"""HTTP errors for failed renders: the legacy ``detail`` text plus a structured ``code``.

Error bodies are ``{"detail": "<text>", "code": "<code>"}``. ``detail`` is unchanged so callers that match
the text keep working; ``code`` (one of ``src.sekai.base.render_errors.RENDER_ERROR_CODES``) is present only
when the failure has a known reason.
"""

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from src.sekai.base.render_errors import render_error_code


class RenderHTTPException(HTTPException):
    def __init__(self, status_code: int, detail: str, code: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


def render_http_exception(exc: BaseException, status_code: int = 500) -> HTTPException:
    """The HTTPException a route raises for ``exc``: a RenderHTTPException when the reason is known."""
    code = render_error_code(exc)
    if code is None:
        return HTTPException(status_code=status_code, detail=str(exc))
    return RenderHTTPException(status_code, str(exc), code)


async def render_http_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RenderHTTPException)
    return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status_code, headers=exc.headers)
