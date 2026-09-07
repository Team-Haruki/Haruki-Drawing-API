"""Native metadata probes must work when Pillow is unavailable."""

import asyncio
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from PIL import Image
import pytest

from src.core.pillow_telemetry import begin_pillow_touch_scope, end_pillow_touch_scope, take_pillow_touch_snapshot
from src.sekai.base import image_info
from src.sekai.base.utils import get_asset_image_ref, get_encoded_image_ref


@pytest.fixture
def native():
    module = image_info._native()
    if module is None:
        pytest.skip("native image info capability required")
    return module


def test_native_probes_do_not_open_pillow_images(native, tmp_path, monkeypatch):
    path = tmp_path / "source.png"
    Image.new("RGBA", (37, 23), (10, 20, 30, 40)).save(path)
    data = path.read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("Pillow decoder reached")

    monkeypatch.setattr(Image, "open", forbidden)
    token = begin_pillow_touch_scope()
    try:
        ref = asyncio.run(get_asset_image_ref(tmp_path, path.name))
        assert ref.size == (37, 23)
        assert get_encoded_image_ref(data).size == ref.size
        assert take_pillow_touch_snapshot().counts == {}
    finally:
        end_pillow_touch_scope(token)


def test_asset_metadata_changes_when_file_is_replaced(native, tmp_path):
    path = tmp_path / "hot.png"
    Image.new("RGB", (20, 30)).save(path)
    first = asyncio.run(get_asset_image_ref(tmp_path, path.name))
    Image.new("RGB", (60, 10)).save(path)
    os.utime(path, ns=(first.mtime_ns + 1_000_000, first.mtime_ns + 1_000_000))
    second = asyncio.run(get_asset_image_ref(tmp_path, path.name))
    assert (first.size, second.size) == ((20, 30), (60, 10))
    assert first.mtime_ns != second.mtime_ns


def test_native_probe_rejects_corrupt_bytes_without_pillow(native, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid native input must not silently use Pillow")

    monkeypatch.setattr(Image, "open", forbidden)
    with pytest.raises(OSError, match="image"):
        image_info.probe_encoded(b"not an image")


def test_native_metadata_imports_and_runs_without_pillow(native, tmp_path):
    path = tmp_path / "source.png"
    Image.new("RGB", (9, 7)).save(path)
    script = r"""
import importlib.abc, sys
class NoPillow(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "PIL" or fullname.startswith("PIL."):
            raise AssertionError("unexpected Pillow import: " + fullname)
sys.meta_path.insert(0, NoPillow())
from pathlib import Path
from src.sekai.base.image_source import AssetImageRef, EncodedImageRef
from src.sekai.base.image_info import probe_asset, probe_encoded
path = Path(sys.argv[1])
assert probe_asset(path)[0] == (9, 7)
assert probe_encoded(path.read_bytes())[0] == (9, 7)
"""
    result = subprocess.run(
        [sys.executable, "-X", "gil=0", "-c", script, str(path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_legacy_probe_is_isolated_and_reported(monkeypatch):
    buffer = BytesIO()
    Image.new("RGB", (5, 3)).save(buffer, format="PNG")
    monkeypatch.setattr(image_info, "_native", lambda: None)
    token = begin_pillow_touch_scope()
    try:
        assert image_info.probe_encoded(buffer.getvalue()) == ((5, 3), "RGB")
        assert take_pillow_touch_snapshot().counts == {"pillow_image_header_probe": 1}
    finally:
        end_pillow_touch_scope(token)


@pytest.mark.parametrize("error", [AttributeError, RuntimeError, OSError])
def test_broken_native_probe_fails_open_for_valid_assets(tmp_path, monkeypatch, error):
    path = tmp_path / "valid.png"
    Image.new("RGB", (13, 17)).save(path)

    def broken(*args):
        raise error("broken native metadata service")

    monkeypatch.setattr(
        image_info, "_native", lambda: SimpleNamespace(asset_image_info=broken, encoded_image_info=broken)
    )
    token = begin_pillow_touch_scope()
    try:
        assert image_info.probe_asset(path) == ((13, 17), "RGB")
        assert image_info.probe_encoded(path.read_bytes()) == ((13, 17), "RGB")
        assert take_pillow_touch_snapshot().counts == {"pillow_image_header_probe": 2}
    finally:
        end_pillow_touch_scope(token)
