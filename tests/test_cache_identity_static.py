import os
from pathlib import Path

from src.core import cache_identity


def _write(root: Path, relative: str, data: bytes, mtime_ns: int | None = None) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


def _digest(root: Path) -> str:
    epoch = cache_identity._StaticEpoch()
    value = epoch.get(root)
    assert value is not None
    return value


def _tree(root: Path) -> None:
    _write(root, "card/frame.png", b"frame")
    _write(root, "chart_asset/style.css", b"css")
    _write(root, "custom_frames/a/horizontal/frame_base.png", b"base")


def test_scratch_material_does_not_change_static_digest(tmp_path):
    _tree(tmp_path)
    before = _digest(tmp_path)
    _write(tmp_path, "outline-candidate/x.png", b"scratch")
    _write(tmp_path, "custom_frames/a/redraw-rest-candidate/y.png", b"scratch")
    _write(tmp_path, "custom_frames/a/vertical-manifest.json", b"{}")
    _write(tmp_path, "custom_frames/a/sprites.tar.gz", b"gz")
    _write(tmp_path, "honor/voice.mp3", b"mp3")
    _write(tmp_path, "_wip/z.png", b"wip")
    _write(tmp_path, ".hidden/z.png", b"hidden")
    _write(tmp_path, "card/.DS_Store.png", b"hidden file")
    assert _digest(tmp_path) == before


def test_pixel_files_change_static_digest(tmp_path):
    _tree(tmp_path)
    before = _digest(tmp_path)
    _write(tmp_path, "custom_frames/a/vertical/frame_base.png", b"new sprite")
    added = _digest(tmp_path)
    assert added != before
    _write(tmp_path, "chart_asset/style.css", b"css v2")
    assert _digest(tmp_path) != added


def test_identical_content_agrees_across_nodes_regardless_of_mtime(tmp_path):
    node_a, node_b = tmp_path / "a", tmp_path / "b"
    for root, mtime in ((node_a, 1_000_000_000), (node_b, 2_000_000_000)):
        _write(root, "card/frame.png", b"frame", mtime)
        _write(root, "bg/1.png", b"bg", mtime)
    assert _digest(node_a) == _digest(node_b)


def test_static_change_refreshes_without_restart(tmp_path, monkeypatch):
    _tree(tmp_path)
    epoch = cache_identity._StaticEpoch()
    monkeypatch.setattr(cache_identity, "_STATIC_RECHECK_SECONDS", 3600.0)
    first = epoch.get(tmp_path)
    _write(tmp_path, "card/frame.png", b"frame v2")
    # Within the recheck window the cached digest is served without walking the tree.
    assert epoch.get(tmp_path) == first
    monkeypatch.setattr(cache_identity, "_STATIC_RECHECK_SECONDS", 0.0)
    assert epoch.get(tmp_path) != first


def test_unchanged_marker_skips_rehash(tmp_path, monkeypatch):
    _tree(tmp_path)
    monkeypatch.setattr(cache_identity, "_STATIC_RECHECK_SECONDS", 0.0)
    epoch = cache_identity._StaticEpoch()
    first = epoch.get(tmp_path)
    calls = []
    original = cache_identity._static_digest
    monkeypatch.setattr(cache_identity, "_static_digest", lambda entries: calls.append(1) or original(entries))
    assert epoch.get(tmp_path) == first
    assert calls == []


def test_renderer_epoch_combines_process_and_static(tmp_path, monkeypatch):
    from src.settings import settings

    _tree(tmp_path)
    monkeypatch.setattr(settings.assets, "base_dir", tmp_path.parent)
    monkeypatch.setattr(settings.assets, "result_asset_path", tmp_path.name)
    monkeypatch.setattr(cache_identity, "_process_epoch", lambda: "p" * 64)
    monkeypatch.setattr(cache_identity, "_STATIC_RECHECK_SECONDS", 0.0)
    monkeypatch.setattr(cache_identity, "_static_epoch", cache_identity._StaticEpoch())
    first = cache_identity.renderer_epoch()
    assert first is not None
    assert len(first) == 64
    _write(tmp_path, "outline-candidate/x.png", b"scratch")
    assert cache_identity.renderer_epoch() == first
    _write(tmp_path, "card/frame.png", b"frame v2")
    assert cache_identity.renderer_epoch() != first
    monkeypatch.setattr(cache_identity, "_process_epoch", lambda: None)
    assert cache_identity.renderer_epoch() is None
