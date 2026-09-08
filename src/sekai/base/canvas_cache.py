"""Prepare shared child canvases, reusing validated native fragments before layout.

Reference/Pillow builders still build the same widget tree. The native canvas renderer
opens a request-scoped preparation context only while evaluating an async canvas factory.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

from .plot import Canvas
from .utils import renderer_code_fingerprint


@dataclass
class CanvasPreparation:
    options: dict
    bg_hour: float
    signatures: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PreparedCanvas:
    context: CanvasPreparation
    key: str
    image: Any = None


_preparation: ContextVar[CanvasPreparation | None] = ContextVar("canvas_preparation", default=None)


def current_canvas_preparation() -> CanvasPreparation | None:
    return _preparation.get()


@contextmanager
def native_canvas_preparation(options: dict, bg_hour: float):
    token = _preparation.set(CanvasPreparation(dict(options), bg_hour))
    try:
        yield
    finally:
        _preparation.reset(token)


class _LeasedCanvas(Canvas):
    """Dimensions plus a strong fragment reference, usable only through paste_canvas."""

    def draw(self, painter):
        # Never silently render an empty tree if a prepared leaf escapes its request
        # or is accidentally used as a page root/reference-backend canvas.
        raise RuntimeError("prepared fragment must be embedded in its native request")


async def prepare_cached_canvas(cache_key: str, factory) -> Canvas:
    """Resolve a child canvas; a native warm hit skips its async asset/layout factory.

    The caller's key covers request/layout/time inputs and missing-asset signatures,
    as for CanvasImageBox.
    The fragment pool additionally validates its recorded assets/fonts. A hit holds its
    immutable pixels through rendering even if another request evicts the cache entry.
    """
    context = current_canvas_preparation()
    if context is None or not cache_key:
        return await factory()
    from src.sekai.skia_renderer import fragment_cache

    if not fragment_cache._cache._enabled():
        return await factory()
    material = ["prepared-canvas", cache_key, context.options, renderer_code_fingerprint()]
    key = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
    image = fragment_cache.get_native_fragment_cached(key, bg_hour=context.bg_hour, signatures=context.signatures)
    if image is None:
        canvas = await factory()
    else:
        canvas = _LeasedCanvas(w=image.width, h=image.height).set_padding(0)
    canvas._native_prepared_canvas = PreparedCanvas(context, key, image)
    return canvas
