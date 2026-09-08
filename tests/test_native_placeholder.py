"""Missing assets remain lazy until their chosen renderer replays them."""

import asyncio
from io import BytesIO
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image, ImageFont
import pytest

from src.core.pillow_telemetry import begin_pillow_touch_scope, end_pillow_touch_scope, take_pillow_touch_snapshot
from src.sekai.base.image_source import MissingImageRef
from src.sekai.base.pillow_placeholder import build_placeholder
from src.sekai.base.placeholder import SIZES
from src.sekai.base.utils import get_asset_image_ref
from src.sekai.skia_renderer.canvas import REQUIRED_NATIVE_IR_CAPABILITY
from src.sekai.skia_renderer.placeholder import render_placeholder

try:
    import haruki_skia_renderer as native
except ImportError:
    native = None

pytestmark = pytest.mark.skipif(
    native is None or getattr(native, "IR_CAPABILITY", 0) < REQUIRED_NATIVE_IR_CAPABILITY,
    reason="native BASIC text and bicubic sampling are required",
)


@pytest.mark.parametrize("variant", SIZES)
def test_placeholder_recipe_parity(variant, monkeypatch):
    from src.sekai.base import pillow_placeholder

    load_font = pillow_placeholder._load_placeholder_font
    monkeypatch.setattr(
        pillow_placeholder,
        "_load_placeholder_font",
        lambda size: load_font(size).font_variant(layout_engine=ImageFont.Layout.BASIC),
    )
    source = MissingImageRef(variant)
    actual = Image.open(BytesIO(render_placeholder(source).data)).convert("RGBA")
    expected = build_placeholder(variant)
    assert actual.size == expected.size == source.size
    diff = np.abs(np.asarray(actual).astype(int) - np.asarray(expected).astype(int))
    assert diff.mean() < 0.25
    assert np.percentile(diff, 99) <= 8


def test_missing_asset_keeps_lazy_placeholder_and_self_heals(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("loading a missing ref must not create Pillow pixels")

    token = begin_pillow_touch_scope()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(Image, "new", forbidden)
            first = asyncio.run(get_asset_image_ref(tmp_path, "banner_event_missing.png"))
            second = asyncio.run(get_asset_image_ref(tmp_path, "banner_event_another.png"))
        assert isinstance(first, MissingImageRef)
        assert first is second  # recipe identity permits one native mem raster per page
        assert first.size == (900, 400)
        Image.new("RGBA", (7, 5)).save(tmp_path / "banner_event_missing.png")
        loaded = asyncio.run(get_asset_image_ref(tmp_path, "banner_event_missing.png"))
        assert loaded.size == (7, 5)
        assert not isinstance(loaded, MissingImageRef)
        assert take_pillow_touch_snapshot().counts == {}
    finally:
        end_pillow_touch_scope(token)


def test_placeholder_runs_in_fresh_interpreter_without_pillow():
    script = """
import importlib.abc, sys
class NoPillow(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "PIL" or fullname.startswith("PIL."):
            raise AssertionError("unexpected Pillow import: " + fullname)
sys.meta_path.insert(0, NoPillow())
from src.sekai.base.image_source import MissingImageRef
from src.sekai.skia_renderer.canvas import REQUIRED_NATIVE_IR_CAPABILITY
from src.sekai.skia_renderer.placeholder import render_placeholder
assert render_placeholder(MissingImageRef()).data.startswith(b"\\x89PNG")
"""
    result = subprocess.run(
        [sys.executable, "-X", "gil=0", "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_batched_refs_keep_order_missing_behavior_and_asset_invalidation(tmp_path):
    from src.sekai.base.utils import get_asset_image_refs

    path = tmp_path / "one.png"
    Image.new("RGBA", (7, 5), "red").save(path)
    refs = asyncio.run(get_asset_image_refs(tmp_path, [path.name, "late.png", None, path.name]))
    assert refs[0] is refs[3]
    assert refs[0].size == (7, 5)
    assert isinstance(refs[1], MissingImageRef)
    assert isinstance(refs[2], MissingImageRef)
    Image.new("RGBA", (11, 9), "blue").save(path)
    Image.new("RGBA", (3, 2), "green").save(tmp_path / "late.png")
    changed = asyncio.run(get_asset_image_refs(tmp_path, [path.name, "late.png"]))
    assert [ref.size for ref in changed] == [(11, 9), (3, 2)]
    assert asyncio.run(get_asset_image_refs(tmp_path, [])) == []
