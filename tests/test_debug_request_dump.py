"""Pins the debug raw-request dump hardening: owner-only permissions and retention.

Dumps carry raw player payloads, so `_dump_request_body` must create the dedicated dir
0700 / files 0600 and prune stale dumps on every write — the operator forgetting to unset
the env must not leave sensitive bodies on disk indefinitely.
"""

from __future__ import annotations

import os
import stat
import time

from src.core.debug import _DUMP_RETENTION_SECONDS, _dump_request_body
from src.settings import settings


def _configure(monkeypatch, tmp_path, prefixes="/api/pjsk/profile/custom-profile-card"):
    monkeypatch.setattr(settings.drawing, "debug_dump_request_dir", tmp_path / "dumps")
    monkeypatch.setattr(settings.drawing, "debug_dump_request_paths", prefixes)


def test_dump_writes_owner_only_file_and_dir(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)

    _dump_request_body("/api/pjsk/profile/custom-profile-card", "req1", b'{"a": 1}')

    dump_dir = tmp_path / "dumps"
    files = list(dump_dir.iterdir())
    assert len(files) == 1
    assert files[0].read_bytes() == b'{"a": 1}'
    assert stat.S_IMODE(dump_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600


def test_dump_prunes_stale_dumps_but_keeps_fresh_ones(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    dump_dir = tmp_path / "dumps"
    dump_dir.mkdir()
    stale = dump_dir / "old_dump.json"
    stale.write_bytes(b"{}")
    expired = time.time() - _DUMP_RETENTION_SECONDS - 60
    os.utime(stale, (expired, expired))
    fresh = dump_dir / "fresh_dump.json"
    fresh.write_bytes(b"{}")

    _dump_request_body("/api/pjsk/profile/custom-profile-card", "req2", b'{"b": 2}')

    names = {p.name for p in dump_dir.iterdir()}
    assert "old_dump.json" not in names
    assert "fresh_dump.json" in names
    assert any(name.endswith("_req2.json") for name in names)


def test_dump_ignores_non_matching_paths(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)

    _dump_request_body("/api/pjsk/card/detail", "req3", b'{"c": 3}')

    assert not (tmp_path / "dumps").exists()
