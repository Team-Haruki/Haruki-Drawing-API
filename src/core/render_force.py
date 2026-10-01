"""Request-local "force a fresh render" flag (Cloud's `--force`, directive header `X-Haruki-Render-Force`).

While it is set, every reader of a reusable render result misses: the composed-image memory and disk
caches, the Skia payload/fragment caches and the asset mirror's NotFound memo. Writers still store, so the
fresh result replaces what later, unforced requests reuse. Asset bytes themselves are not refetched:
their caches are keyed by content signature and asset revision, so they cannot hold stale pixels.

`src.core` rather than `src.sekai.base.utils` for the same reason as `missing_asset_telemetry`: the debug
middleware binds it, and `utils` already imports `debug`. Heavy workers receive it on the task.
"""

from __future__ import annotations

import contextvars

_forced: contextvars.ContextVar[bool] = contextvars.ContextVar("render_force", default=False)


def begin_render_force(forced: bool) -> contextvars.Token[bool]:
    return _forced.set(bool(forced))


def end_render_force(token: contextvars.Token[bool]) -> None:
    _forced.reset(token)


def render_forced() -> bool:
    return _forced.get()
