"""AST guard: every drawing route awaits the (async) exit.

`encoded_image_payload_to_response` became a coroutine function when it learned to upload artifacts. A route
that forgets the `await` returns a coroutine object instead of a Response — FastAPI would then try to
serialise it and the request fails. This pins the `await` structurally for every `/api/pjsk*` route.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from tests.test_route_render_contract import _drawing_routes

EXIT = "encoded_image_payload_to_response"


def _exit_calls_and_awaited(tree: ast.AST) -> tuple[int, int]:
    awaited = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Await)}
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == EXIT)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == EXIT)
        )
    ]
    return len(calls), sum(1 for call in calls if id(call) in awaited)


@pytest.mark.parametrize(("path", "endpoint"), _drawing_routes())
def test_every_route_awaits_the_exit(path, endpoint):
    tree = ast.parse(textwrap.dedent(inspect.getsource(endpoint)))
    calls, awaited = _exit_calls_and_awaited(tree)
    assert calls >= 1, f"{path} does not leave through {EXIT}"
    assert awaited == calls, f"{path}: {calls - awaited} call(s) to {EXIT} are not the direct child of an await"


def test_the_guard_catches_a_missing_await():
    tree = ast.parse(f"async def route(p):\n    return {EXIT}(p)\n")
    assert _exit_calls_and_awaited(tree) == (1, 0)
    tree = ast.parse(f"async def route(p):\n    return await {EXIT}(p)\n")
    assert _exit_calls_and_awaited(tree) == (1, 1)
