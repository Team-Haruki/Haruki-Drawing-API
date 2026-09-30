"""`get_asset_image_refs`: cold mirror keys overlap on the fetch pool instead of queueing inside a render task."""

from __future__ import annotations

import asyncio
import io
import threading
import time

from PIL import Image
import pytest

from src.assets.mirror import AssetMirror, MirrorStats, set_asset_mirror
from src.assets.version import StaticVersion
from src.sekai.base import utils
from src.settings import AssetMirrorSettings, settings
from tests.storage_fakes import FakeObjectStore

_LOGICAL = "asset/jp-assets/startapp/music/jacket/j{:03d}.png"
_OBJECT_KEY = "jp-assets/startapp/music/jacket/j{:03d}.png"


def _png_bytes(size: tuple[int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, (1, 2, 3, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


class _ConcurrencyProbe(FakeObjectStore):
    """Records how many remote reads overlap; each read blocks its caller for `delay` seconds."""

    def __init__(self, **kwargs) -> None:
        super().__init__(bucket="pjsk-assets", delay=0.1, **kwargs)
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0

    async def read(self, key: str, *, max_bytes: int | None = None):
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            return await super().read(key, max_bytes=max_bytes)
        finally:
            with self._lock:
                self.active -= 1


@pytest.fixture
def fetch_pool_reset():
    utils.shutdown_asset_fetch_pool()
    yield
    utils.shutdown_asset_fetch_pool()


@pytest.fixture
def probe_mirror(tmp_path, monkeypatch, fetch_pool_reset):
    monkeypatch.setattr(settings.assets.mirror, "fetch_concurrency", 6)
    store = _ConcurrencyProbe()
    mirror = AssetMirror(
        base_dir=tmp_path,
        settings=AssetMirrorSettings(fetch_concurrency=6),
        store_factory=lambda region: store,
        version_source=StaticVersion("v0"),
        stats=MirrorStats(),
    )
    utils.clear_resolved_path_cache()
    utils._load_asset_image_ref_cached.cache_clear()
    set_asset_mirror(mirror)
    try:
        yield mirror, store
    finally:
        set_asset_mirror(None)
        mirror.close()
        utils.clear_resolved_path_cache()
        utils._load_asset_image_ref_cached.cache_clear()


def test_cold_keys_of_one_batch_are_fetched_concurrently_within_the_bound(tmp_path, probe_mirror) -> None:
    _mirror, store = probe_mirror
    for index in range(12):
        store.objects[_OBJECT_KEY.format(index)] = _png_bytes((index + 1, 3))

    started = time.perf_counter()
    refs = asyncio.run(utils.get_asset_image_refs(tmp_path, [_LOGICAL.format(i) for i in range(12)]))
    elapsed = time.perf_counter() - started

    # One batch of 12 cold keys: serial would be 12 x 0.1 s; six fetch slots take two rounds.
    assert 1 < store.peak <= 6
    assert elapsed < 0.9
    assert [ref.size for ref in refs] == [(i + 1, 3) for i in range(12)]
    assert all(ref.path == tmp_path / "mirror" / "v0" / _OBJECT_KEY.format(i) for i, ref in enumerate(refs))
    assert sorted(store.reads) == sorted(_OBJECT_KEY.format(i) for i in range(12))


def test_mixed_batch_keeps_order_placeholders_and_local_hits(tmp_path, probe_mirror) -> None:
    _mirror, store = probe_mirror
    store.objects[_OBJECT_KEY.format(1)] = _png_bytes((5, 5))
    warm = tmp_path / "mirror" / "v0" / _OBJECT_KEY.format(0)
    warm.parent.mkdir(parents=True, exist_ok=True)
    warm.write_bytes(_png_bytes((4, 4)))
    local = tmp_path / "static_images" / "x.png"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(_png_bytes((2, 2)))

    keys = [
        _LOGICAL.format(0),  # already mirrored: resolved inside the batch, no read
        _LOGICAL.format(1),  # cold: fetched on the fetch pool
        "static_images/x.png",  # not a bucket key
        _LOGICAL.format(2),  # absent remotely: placeholder
        None,
        [_LOGICAL.format(3), "static_images/x.png"],  # cold first candidate, local second
    ]
    refs = asyncio.run(utils.get_asset_image_refs(tmp_path, keys))

    found = [ref if isinstance(ref, utils.AssetImageRef) else None for ref in refs]
    assert [ref.size if ref else None for ref in found] == [(4, 4), (5, 5), (2, 2), None, None, (2, 2)]
    assert found[5].path == local.resolve()
    assert _OBJECT_KEY.format(0) not in store.reads
    assert {_OBJECT_KEY.format(1), _OBJECT_KEY.format(2), _OBJECT_KEY.format(3)} <= set(store.reads)


def test_warm_batch_never_touches_the_fetch_pool(tmp_path, probe_mirror) -> None:
    for index in range(3):
        path = tmp_path / "mirror" / "v0" / _OBJECT_KEY.format(index)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_png_bytes((3, 3)))

    refs = asyncio.run(utils.get_asset_image_refs(tmp_path, [_LOGICAL.format(i) for i in range(3)]))

    assert all(isinstance(ref, utils.AssetImageRef) for ref in refs)
    assert utils._asset_fetch_pool is None


def test_signature_on_the_event_loop_skips_the_mirror(tmp_path, probe_mirror) -> None:
    mirror, store = probe_mirror
    store.objects[_OBJECT_KEY.format(7)] = _png_bytes((3, 3))

    async def on_loop():
        return utils.get_image_asset_signature(tmp_path, _LOGICAL.format(7))

    assert asyncio.run(on_loop()) == {"source_path": _LOGICAL.format(7), "missing": True}
    assert mirror.stats.snapshot()["skipped_on_loop"] == 0
    assert store.reads == []

    # Off the loop the signature still resolves through the mirror, as before.
    signature = utils.get_image_asset_signature(tmp_path, _LOGICAL.format(7))
    assert signature["resolved_path"] == str(tmp_path / "mirror" / "v0" / _OBJECT_KEY.format(7))
    assert store.reads == [_OBJECT_KEY.format(7)]
