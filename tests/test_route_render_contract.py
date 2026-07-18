"""Structural anti-drift guards over the drawing routes.

These do not check pixels. They check that the ARCHITECTURE holds as endpoints get added, because
both failures this repo actually suffered were structural, not visual:

- an endpoint quietly shipping without a Skia path, so it silently stayed Pillow-only forever;
- an endpoint hand-building its own IR scene, so its layout existed twice in Python and drifted
  (that is what retired ``skia_renderer/card_render.py``).

A new endpoint that trips these is not necessarily wrong — but it must be an explicit, argued
exemption in the lists below, not an accident.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import src.core.main as main_mod

REPO_ROOT = Path(__file__).resolve().parent.parent


def _walk(router, prefix: str = ""):
    """FastAPI wraps included routers instead of flattening them, so recurse."""
    for route in getattr(router, "routes", []):
        inner = getattr(route, "original_router", None)
        if inner is not None:
            ctx = getattr(route, "include_context", None)
            yield from _walk(inner, prefix + (getattr(ctx, "prefix", "") or ""))
            continue
        path = getattr(route, "path", None)
        endpoint = getattr(route, "endpoint", None)
        if path and endpoint:
            yield prefix + path, endpoint


def _drawing_routes() -> list[tuple[str, object]]:
    return [(p, fn) for p, fn in _walk(main_mod.app) if p.startswith("/api/pjsk")]


# Routes that legitimately do not render through the Skia shadow layer. Each needs a reason.
_NO_SKIA_PATH: dict[str, str] = {}

# Routes whose render happens inside a spawned heavy-worker process, so the Skia call is in
# heavy_render_pool, not in the route body. They DO go through the shadow layer.
_HEAVY_WORKER_ROUTES = {
    "/api/pjsk/deck/recommend",
    "/api/pjsk/misc/chara-birthday",
}


def test_every_drawing_route_has_a_skia_path():
    """A new drawing endpoint must go through the shadow layer, or say why not.

    Without this, an endpoint added during the migration silently renders Pillow-only forever —
    it never shows up as a failure, just as a permanent absence from /render-stats.
    """
    missing = []
    for path, endpoint in _drawing_routes():
        if path in _NO_SKIA_PATH or path in _HEAVY_WORKER_ROUTES:
            continue
        source = inspect.getsource(endpoint)
        if "try_render" not in source:
            missing.append(f"{path} -> {endpoint.__module__}.{endpoint.__name__}")

    assert not missing, (
        "these drawing routes never call a try_render_*_payload, so they are Pillow-only:\n  "
        + "\n  ".join(missing)
        + "\nEither wire the shadow layer, or add the route to _NO_SKIA_PATH with a reason."
    )


def test_the_heavy_worker_routes_really_do_render_via_skia():
    """_HEAVY_WORKER_ROUTES exempts two routes from the source scan because their Skia call lives in
    the worker, not the route. Prove that is still true, or the exemption becomes a hiding place."""
    source = (REPO_ROOT / "src/core/heavy_render_pool.py").read_text(encoding="utf-8")
    assert "try_render_deck_recommend_payload" in source
    assert "try_render_chara_birthday_payload" in source


# Modules allowed to hand-build an IR scene instead of drawing a plot.py widget tree.
# Adding to this list means accepting that the layout now exists twice in Python.
_MAY_HAND_BUILD_IR = {
    "src/sekai/skia_renderer/ir_builder.py",  # the builder itself
    "src/sekai/skia_renderer/ir_painter.py",  # the widget-tree lowering
    "src/sekai/skia_renderer/canvas.py",  # lowers a widget tree via build_canvas_ir()
    # The chart image comes from the pjsekai-scores-rs crate on BOTH backends; the IR here is only
    # the watermark footer shell around that raster, not a second layout of the chart.
    "src/sekai/chart/drawer.py",
    # Same shape as chart: the badge is the shared HonorBadgeBox widget tree (spliced in via
    # build_canvas_ir); the IR built here is ONLY the raster watermark footer the route adds after
    # the compose — a SelfImage snapshot of the canvas, which no widget can express.
    "src/sekai/honor/skia.py",
    # The custom profile card has no plot.py tree to lower: its layout carrier is the Unity card
    # JSON, flattened and rasterized by the existing PNGRenderer on BOTH backends (the scene here
    # places those shared rasters with Transform nodes built from the same layer_transform_inputs
    # numbers the Pillow compositor consumes). The Pillow renderer stays the parity baseline —
    # same category as the chart/honor shells, argued in docs/custom-profile-skia-feasibility.md.
    "src/sekai/profile/custom_profile/skia.py",
}


def test_no_new_hand_written_scene_builders():
    """The widget tree is the only layout carrier. A dedicated IR scene builder means the layout is
    written twice and has to be kept in step by hand — which is exactly how card/list drifted from
    its Pillow tree, and why card_render.py was deleted."""
    # Watch BOTH doors. Constructing an IRBuilder is the obvious one. The subtle one is
    # build_canvas_ir(): it hands back a LIVE, MUTABLE builder so a widget tree can be spliced into
    # a larger scene — which also lets a caller emit its own .image()/.text() nodes onto it and
    # hand-build a second layout without ever naming IRBuilder. Guarding only the first door leaves
    # the second one standing open.
    #
    # Scan the IMPORTS, not the raw text: you cannot use either without importing it, and a text
    # scan trips over any comment that merely names them (this test's own explanation did).
    doors = {"IRBuilder", "build_canvas_ir"}
    offenders = []
    for py in (REPO_ROOT / "src" / "sekai").rglob("*.py"):
        rel = py.relative_to(REPO_ROOT).as_posix()
        if rel in _MAY_HAND_BUILD_IR:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(alias.name in doors for alias in node.names):
                offenders.append(rel)
                break

    assert not offenders, (
        "these modules hand-build an IR scene (or take a mutable builder from build_canvas_ir) "
        "instead of drawing the shared widget tree:\n  "
        + "\n  ".join(offenders)
        + "\nDraw a plot.py widget tree both backends can render, or add an argued exemption to "
        "_MAY_HAND_BUILD_IR."
    )


@pytest.mark.parametrize(("path", "endpoint"), _drawing_routes())
def test_drawing_routes_fall_back_to_pillow(path, endpoint):
    """FAIL-OPEN: a Skia problem must degrade to Pillow, never 500. Every route that calls
    try_render must also have a Pillow compose path to fall back to."""
    if path in _NO_SKIA_PATH:
        pytest.skip(_NO_SKIA_PATH[path])
    source = inspect.getsource(endpoint)
    if "try_render" not in source:
        pytest.skip("no Skia path (covered by test_every_drawing_route_has_a_skia_path)")

    assert "compose" in source, (
        f"{path} calls try_render but has no Pillow compose fallback; a Skia failure would 500 instead of degrading"
    )
