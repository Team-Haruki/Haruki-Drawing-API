"""Backend-neutral encoded image response payloads."""

from dataclasses import dataclass


@dataclass(slots=True)
class EncodedImagePayload:
    image_bytes: bytes
    media_type: str
    filename: str
    image_width: int | None
    image_height: int | None
    image_mode: str | None
    encode_elapsed: float
    native_metrics: dict[str, int | float] | None = None
    # skia | skia_cache | skia_fallback | pillow — stamped by the renderer. Heavy tasks
    # run in a spawned process where a contextvar is invisible to the parent, so the backend
    # rides back on the payload; None means the parent must resolve it from local context.
    backend: str | None = None
    # Request-scoped Pillow touches consumed when the worker recorded this native render.
    # ``None`` means telemetry was unavailable, ``{}`` proves native-pure, and a non-empty
    # mapping classifies the render as native-hybrid in the parent process.
    pillow_touch_counts: dict[str, int] | None = None
