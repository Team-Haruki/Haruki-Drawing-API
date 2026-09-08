from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import os
from pathlib import Path
import time
from typing import Any

from PIL import Image
import pytest

from src.sekai.base import painter as painter_mod
from src.sekai.base.painter import Painter


def make_painter() -> Painter:
    painter = Painter(size=(4, 4))
    painter.rect((0, 0), (4, 4), (10, 20, 30, 255))
    return painter


def test_painter_disk_cache_miss_then_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(painter_mod, "PAINTER_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(painter_mod, "DEBUG", False)

    first = make_painter()
    first_image = asyncio.run(first.get("page"))
    cache_files = list(tmp_path.glob("page__*.png"))
    assert len(cache_files) == 1
    assert first.operations == []

    second = make_painter()
    second_image = asyncio.run(second.get("page"))
    assert second_image.tobytes() == first_image.tobytes()
    assert second.operations != []


def test_painter_replaces_stale_disk_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(painter_mod, "PAINTER_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(painter_mod, "DEBUG", False)
    stale_path = tmp_path / "page__stale.png"
    Image.new("RGBA", (1, 1), "red").save(stale_path)

    output = asyncio.run(make_painter().get("page"))

    assert output.size == (4, 4)
    assert not stale_path.exists()
    assert len(list(tmp_path.glob("page__*.png"))) == 1


def test_disk_cache_loader_returns_detached_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(painter_mod, "PAINTER_CACHE_DIR", str(tmp_path))
    assert Painter._load_disk_cache("missing", "hash") is None

    path = tmp_path / "page__hash.png"
    Image.new("RGBA", (2, 3), "blue").save(path)
    image = Painter._load_disk_cache("page", "hash")

    assert image is not None
    assert image.size == (2, 3)
    assert image.getpixel((0, 0)) == (0, 0, 255, 255)


def test_stale_cache_removal_logs_individual_failure(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removed: list[str] = []

    def remove(path: str) -> None:
        if path == "bad":
            raise OSError("busy")
        removed.append(path)

    monkeypatch.setattr(painter_mod.os, "remove", remove)
    with caplog.at_level(logging.WARNING):
        Painter._remove_stale_cache_files(["good", "bad"])

    assert removed == ["good"]
    assert "Failed to remove cache file bad" in caplog.records[-1].message


def test_execute_operations_clears_queue_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    painter = make_painter()

    async def fail_execute(_func: Any, *_args: Any) -> None:
        raise RuntimeError("draw failed")

    monkeypatch.setattr(painter_mod, "run_in_pool", fail_execute)
    with pytest.raises(RuntimeError, match="draw failed"):
        asyncio.run(painter._execute_operations())
    assert painter.operations == []


def test_save_disk_cache_is_fail_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    painter = Painter(img=Image.new("RGBA", (1, 1)))
    monkeypatch.setattr(painter_mod, "PAINTER_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(painter_mod.os, "makedirs", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("full")))

    painter._save_disk_cache("page", "hash")

    assert list(tmp_path.iterdir()) == []


def test_get_without_cache_executes_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(painter_mod, "DEBUG", False)
    painter = make_painter()

    image = asyncio.run(painter.get())

    assert image.size == (4, 4)
    assert image.getpixel((0, 0)) == (10, 20, 30, 255)


def test_cache_maintenance_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(painter_mod, "PAINTER_CACHE_DIR", str(tmp_path))
    page = tmp_path / "page__one.png"
    other = tmp_path / "other__two.png"
    page.write_bytes(b"page")
    other.write_bytes(b"other")
    old_time = time.time() - 10 * 86400
    os.utime(page, (old_time, old_time))

    mtimes = Painter.get_cache_key_mtimes()
    assert set(mtimes) == {"page", "other"}
    assert isinstance(mtimes["page"], datetime)

    assert Painter.cleanup_old_disk_cache(max_age_days=7) == 1
    assert not page.exists()
    assert other.exists()
    assert Painter.clear_cache("other") == 1
    assert not other.exists()


def test_cache_cleanup_and_clear_ignore_remove_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(painter_mod, "PAINTER_CACHE_DIR", str(tmp_path))
    path = tmp_path / "page__hash.png"
    path.write_bytes(b"cache")
    monkeypatch.setattr(painter_mod.os, "remove", lambda _path: (_ for _ in ()).throw(OSError("busy")))

    assert Painter.clear_cache("page") == 0
    assert Painter.cleanup_old_disk_cache(max_age_days=-1) == 0


def test_shutdown_painter_delegates_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(Painter, "cleanup_old_disk_cache", lambda: calls.append(1) or 0)

    painter_mod.shutdown_painter()

    assert calls == [1]
