"""Shared doubles for the route tests.

The route exit `encoded_image_payload_to_response` is async (it may upload an artifact), and every route
awaits it. A sync lambda stub would hand the route a non-awaitable and fail; this is the one shared stub.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest


def async_exit_stub(payload: object, response: object) -> Callable[[object], Awaitable[object]]:
    """An `async def _exit(value)` that returns `response` for exactly `payload`, and fails otherwise."""

    async def _exit(value: object) -> object:
        if value is not payload:
            pytest.fail("unexpected payload")
        return response

    return _exit
