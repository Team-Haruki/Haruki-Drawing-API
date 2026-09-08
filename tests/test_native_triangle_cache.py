"""Background tiles preserve the clock, clipping, content and bounded-cache semantics."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

try:
    import haruki_skia_renderer as native
except ImportError:
    native = None

pytestmark = pytest.mark.skipif(
    native is None or "background_cache_hits" not in native.renderer_cache_stats(),
    reason="native background tile cache required",
)


def scene(width=180, height=1100, hour=12.0):
    return {
        "version": 2,
        "assets_base_dir": ".",
        "fonts": {
            "dir": str(Path(__file__).resolve().parents[1] / "data"),
            "default": "SourceHanSansSC-Regular",
            "bold": "SourceHanSansSC-Bold",
        },
        "canvas": {"width": width, "height": height},
        "background": {"type": "TriangleBg", "hour": hour, "tris": [[62.25, 120.5, 12.5, 50, 200, 220, 250, 143, 1]]},
        "root": {
            "type": "Group",
            "children": [{"type": "Rect", "pos": [3, 4], "size": [40, 20], "fill": [255, 0, 0, 255]}],
        },
    }


def render(value):
    return native.render_scene(json.dumps(value).encode(), {})["image_bytes"]


def test_background_hit_does_not_cache_foreground_and_clear_rebuilds():
    native.clear_renderer_caches()
    value = scene()
    first = render(value)
    assert render(value) == first
    assert native.renderer_cache_stats()["background_cache_hits"] == 1
    value["root"]["children"][0]["fill"] = [0, 0, 255, 255]
    second = render(value)
    assert second != first
    assert native.renderer_cache_stats()["background_cache_hits"] == 2
    native.clear_renderer_caches()
    assert render(value) == second
    assert native.renderer_cache_stats()["background_cache_misses"] == 1


def test_palette_uses_resolved_color_not_a_frozen_hour_bucket():
    native.clear_renderer_caches()
    value = scene()
    first = render(value)
    value["background"]["hour"] = 12.00001
    assert render(value) == first
    assert native.renderer_cache_stats()["background_cache_hits"] == 1
    value["background"]["hour"] = 12.5
    second = render(value)
    assert second != first
    assert native.renderer_cache_stats()["background_cache_misses"] == 2
    native.clear_renderer_caches()
    assert render(value) == second


@pytest.mark.parametrize("change", ["scatter", "dimensions", "hue"])
def test_drawing_input_changes_invalidate(change):
    native.clear_renderer_caches()
    value = scene()
    first = render(value)
    if change == "scatter":
        value["background"]["tris"][0][0] += 11.75
    elif change == "dimensions":
        value["canvas"]["width"] += 11
    else:
        value["background"].update(time_color=False, main_hue=0.3)
    second = render(value)
    assert second != first
    assert native.renderer_cache_stats()["background_cache_misses"] == 2
    assert render(value) == second
    native.clear_renderer_caches()
    assert render(value) == second


def test_nested_clipped_background_never_aliases_full_background():
    native.clear_renderer_caches()
    value = scene()
    render(value)
    bg = value.pop("background")
    value["root"]["children"] = [
        {"type": "Group", "offset": [0.25, 0.25], "size": [179.5, 1099.5], "clip": {"kind": "rect"}, "children": [bg]}
    ]
    result = render(value)
    assert native.renderer_cache_stats()["background_cache_hits"] == 0
    native.clear_renderer_caches()
    assert render(value) == result
    assert native.renderer_cache_stats()["raster_cache_entries"] == 0


def test_scaled_background_uses_original_path():
    native.clear_renderer_caches()
    value = scene()
    value["scale"] = 1.25
    first = render(value)
    assert render(value) == first
    stats = native.renderer_cache_stats()
    assert stats["background_cache_hits"] == 0
    assert stats["background_cache_bypasses"] == 2


def test_tight_scene_budget_declines_optional_capture_without_failing():
    native.clear_renderer_caches()
    value = scene()
    first = render(value)
    native.clear_renderer_caches()
    value["limits"] = {"max_scene_bytes": 180 * 1100 * 4, "max_node_pixels": 180 * 1100}
    assert render(value) == first
    stats = native.renderer_cache_stats()
    assert stats["raster_cache_entries"] == 0
    assert stats["background_cache_hits"] == 0


def test_concurrent_foregrounds_and_eviction_do_not_mix_backgrounds():
    values = [scene(hour=hour) for hour in (0, 6, 12, 18)]
    expected = []
    for value in values:
        native.clear_renderer_caches()
        expected.append(render(value))

    def run(i):
        if i % 3 == 0:
            native.clear_renderer_caches()
        return render(values[i % len(values)])

    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, result in enumerate(pool.map(run, range(24))):
            assert result == expected[i % len(expected)]


def test_disabled_and_small_pool_match_and_respect_shared_capacity():
    values = [scene(hour=hour) for hour in (0, 3, 6, 9, 12, 15, 18, 21)]
    values += [deepcopy(value) for value in values]
    script = """
import hashlib,json,sys
import haruki_skia_renderer as native
values=json.loads(sys.stdin.read())
images=[]
for value in values:
    data=native.render_scene(json.dumps(value).encode(), {})['image_bytes']
    images.append(hashlib.sha256(data).hexdigest())
print(json.dumps({'images':images,'stats':native.renderer_cache_stats()}))
"""
    results = []
    for mb in (0, 4, 64):
        result = subprocess.run(
            [sys.executable, "-X", "gil=0", "-c", script],
            input=json.dumps(values),
            text=True,
            capture_output=True,
            env={**os.environ, "HARUKI_SKIA_RASTER_CACHE_MB": str(mb), "HARUKI_SKIA_RASTER_CACHE_MAX_ENTRY_MB": "1"},
            check=True,
            timeout=60,
        )
        value = json.loads(result.stdout)
        assert value["stats"]["raster_cache_bytes"] <= mb * 1024 * 1024
        results.append(value)
    assert results[0]["images"] == results[1]["images"] == results[2]["images"]
    assert results[0]["stats"]["background_cache_hits"] == 0
    assert results[2]["stats"]["background_cache_hits"] >= 8


def test_warm_hit_also_respects_scene_retention_budget():
    native.clear_renderer_caches()
    value = scene()
    expected = render(value)
    value["limits"] = {"max_scene_bytes": 180 * 1100 * 4, "max_node_pixels": 180 * 1100}
    assert render(value) == expected
    stats = native.renderer_cache_stats()
    assert stats["background_cache_hits"] == 0
    assert stats["background_cache_bypasses"] == 1


def test_generated_scatter_tracks_real_clock_and_hour_boundary():
    from src.sekai.base.triangle_bg import build_triangle_bg

    native.clear_renderer_caches()
    results = []
    # Exercise ordinary fractional seconds, color changes and the hourly scatter reset.
    for hour in (12.25, 12.25 + 1 / 3600, 12.5, 12.99999, 13.0):
        value = scene(hour=hour)
        spec = build_triangle_bg(180, 1100, hour, True, None, 0.0)
        value["background"]["tris"] = [[t.x, t.y, t.rot, t.size, *t.color, t.type] for t in spec.triangles]
        rendered = render(value)
        assert render(value) == rendered
        results.append((value, rendered))
    assert results[-2][1] != results[-1][1]
    for value, rendered in results:
        native.clear_renderer_caches()
        assert render(value) == rendered
