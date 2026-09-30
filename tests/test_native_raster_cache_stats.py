"""Process-wide asset raster pool counters reach `/cache/stats` and reset with the runtime clear."""

import json
from pathlib import Path

from PIL import Image
import pytest

try:
    import haruki_skia_renderer as native
except ImportError:
    native = None

pytestmark = pytest.mark.skipif(
    native is None or "raster_cache_evictions" not in native.renderer_cache_stats(),
    reason="native raster cache counters required",
)


def _scene(base: Path) -> dict:
    return {
        "version": 2,
        "assets_base_dir": str(base),
        "fonts": {
            "dir": str(Path(__file__).resolve().parents[1] / "data"),
            "default": "SourceHanSansSC-Regular",
            "bold": "SourceHanSansSC-Bold",
        },
        "canvas": {"width": 64, "height": 64},
        "root": {
            "type": "Group",
            "children": [{"type": "Image", "path": "icon.png", "pos": [4, 4], "size": [20, 20]}],
        },
    }


def test_asset_raster_hits_and_misses_are_counted_across_renders(tmp_path):
    Image.new("RGBA", (40, 40), (200, 30, 90, 255)).save(tmp_path / "icon.png")
    scene = json.dumps(_scene(tmp_path)).encode()
    native.clear_renderer_caches()
    assert native.renderer_cache_stats()["raster_cache_hits"] == 0

    first = native.render_scene(scene, {})
    second = native.render_scene(scene, {})
    assert second["image_bytes"] == first["image_bytes"]

    stats = native.renderer_cache_stats()
    if stats["raster_cache_max_bytes"] == 0:
        assert stats["raster_cache_hits"] == stats["raster_cache_misses"] == 0
        return
    assert stats["raster_cache_misses"] >= 1
    assert stats["raster_cache_hits"] >= 1
    assert stats["raster_cache_evictions"] == 0
    assert stats["raster_cache_entries"] >= 1

    native.clear_renderer_caches()
    stats = native.renderer_cache_stats()
    assert stats["raster_cache_hits"] == stats["raster_cache_misses"] == stats["raster_cache_entries"] == 0
