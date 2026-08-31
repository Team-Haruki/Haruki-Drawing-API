"""The triangle background is generated once and drawn by both backends.

It used to be rolled twice: Painter scattered from Python's unseeded global ``random``, the Rust
renderer from its own xorshift seeded on ``(width, height, hour)`` at millisecond precision. So the
two backends could never agree, neither reproduced itself, and every pixel-exact regression check
had to route around the background. These tests pin the three properties that fix bought.
"""

from __future__ import annotations

import hashlib
import random

import pytest

from src.sekai.base.triangle_bg import build_triangle_bg, triangle_bg_seed

ARGS = (900, 600, 15.5, True, None, 0.0)


def test_the_scatter_is_deterministic():
    a = build_triangle_bg(*ARGS)
    b = build_triangle_bg(*ARGS)
    assert a.triangles == b.triangles
    assert len(a.triangles) > 50, "a 900x600 canvas should scatter a few hundred triangles"


def test_the_global_rng_cannot_reach_the_scatter():
    """The actual regression. The old code called ``random.uniform`` on the module-level RNG, so
    the background depended on whatever else in the process had drawn from it — which is why two
    renders of the same tree differed by ~12% of pixels and why the legacy-baseline harness once
    reported that everything had drifted when nothing had."""
    random.seed(1)
    a = build_triangle_bg(*ARGS)
    random.seed(999_999)
    for _ in range(1000):
        random.random()  # churn the global RNG as hard as a real request would
    b = build_triangle_bg(*ARGS)
    assert a.triangles == b.triangles


def test_both_backends_are_handed_the_same_list():
    """Painter draws ``spec.triangles``; IRPainter serializes the same tuple onto TriangleBg.tris.
    If these ever diverge, the backends silently draw different backgrounds again."""
    from src.sekai.skia_renderer.ir_painter import IRPainter

    p = IRPainter(
        (900, 600),
        assets_base_dir="/base",
        font_dir="/fonts",
        default_font="Regular",
        bold_font="Bold",
        bg_hour=15.5,
    )
    p.draw_random_triangle_bg(True, None, 0.0)
    node = p.builder.build()["background"]

    spec = build_triangle_bg(*ARGS)
    assert node["type"] == "TriangleBg"
    assert node["tris"] == [[t.x, t.y, t.rot, t.size, *t.color, t.type] for t in spec.triangles]


def test_the_seed_is_stable_within_the_hour_but_not_across_it():
    """The layout is pinned to the whole hour so a render reproduces (and a raster cache can key on
    it); the palette keeps the fractional hour and goes on shifting smoothly. Getting this backwards
    — the old Rust seed took the fractional hour — is what made 'deterministic' change every 3.6s."""
    within = {triangle_bg_seed(900, 600, h, True, 0.0, 0.0) for h in (15.0, 15.3, 15.99)}
    assert len(within) == 1

    across = {triangle_bg_seed(900, 600, h, True, 0.0, 0.0) for h in (14.5, 15.5, 16.5)}
    assert len(across) == 3


def test_the_seed_separates_canvases_and_palettes():
    base = triangle_bg_seed(900, 600, 15.5, True, 0.0, 0.0)
    assert triangle_bg_seed(901, 600, 15.5, True, 0.0, 0.0) != base  # width
    assert triangle_bg_seed(900, 601, 15.5, True, 0.0, 0.0) != base  # height
    assert triangle_bg_seed(900, 600, 15.5, False, 0.0, 0.0) != base  # time palette vs custom hue
    assert triangle_bg_seed(900, 600, 15.5, True, 0.05, 0.0) != base  # main hue


def test_triangles_stay_on_the_canvas_and_carry_a_visible_alpha():
    spec = build_triangle_bg(*ARGS)
    for t in spec.triangles:
        assert 0 <= t.x < 900
        assert 0 <= t.y < 600
        assert 1 <= t.size <= 1000
        assert 34 < t.color[3] <= 255, "the generator drops triangles too faint to see"
        assert t.type in (0, 1, 2)


def test_the_custom_hue_palette_differs_from_the_time_palette():
    time_spec = build_triangle_bg(900, 600, 15.5, True, None, 0.0)
    hue_spec = build_triangle_bg(900, 600, 15.5, False, 0.05, 0.0)
    assert time_spec.grad1 != hue_spec.grad1
    assert time_spec.white_alpha != hue_spec.white_alpha or time_spec.grad2 != hue_spec.grad2


@pytest.mark.parametrize(
    ("args", "expected_digest"),
    [
        ((900, 600, 15.5, True, None, 0.0), "3dd53203551e053ca348ae89eadcac0c85f0428f3856560977299d45a367b5a2"),
        ((1536, 880, 2.25, False, 0.31, 0.4), "ba7d5dc59b6c9b362256a3a32cb6367801fdc3b1e6e5625ed86537488de2fbcc"),
        ((400, 1200, 23.9, True, None, 1.0), "3ff2a6033fc0492829baca7edd6d378ee89630540d43fcba62c54dff7d14e7ea"),
    ],
)
def test_triangle_spec_keeps_the_pre_refactor_sequence(args, expected_digest) -> None:
    digest = hashlib.sha256(repr(build_triangle_bg(*args)).encode()).hexdigest()
    assert digest == expected_digest
