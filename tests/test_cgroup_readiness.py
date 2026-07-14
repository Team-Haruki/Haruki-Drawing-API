"""/ready has to see the memory that actually fills the container.

``rss_mb`` reads /proc/self/status VmRSS, which is the PARENT process only. The heavy-render
workers are separate processes and are where most of the container's memory lives (~500 MB each
once warm, against a parent that peaks under 1 GB), so an RSS gate cannot fire before the kernel
OOM-kills the cgroup. ``read_cgroup_memory()`` reads the cgroup instead.

Every failure mode here fails OPEN -- an unreadable or misparsed limit means "no gate" -- so the
ways it can be silently wrong are what these tests pin.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core import debug


def _fake_cgroup(tmp_path: Path, usage: str, limit: str, *, v1: bool = False) -> tuple[Path, Path]:
    usage_name, limit_name = ("memory.usage_in_bytes", "memory.limit_in_bytes") if v1 else ("current", "max")
    usage_path = tmp_path / usage_name
    limit_path = tmp_path / limit_name
    usage_path.write_text(usage)
    limit_path.write_text(limit)
    return usage_path, limit_path


@pytest.fixture
def cgroup_files(monkeypatch):
    def _install(pairs):
        monkeypatch.setattr(debug, "_CGROUP_MEMORY_FILES", tuple(pairs))

    return _install


def test_a_limited_cgroup_reports_usage_and_limit(tmp_path, cgroup_files):
    cgroup_files([_fake_cgroup(tmp_path, str(2 * 1024**3), str(4 * 1024**3))])
    usage_mb, limit_mb = debug.read_cgroup_memory()
    assert usage_mb == pytest.approx(2048)
    assert limit_mb == pytest.approx(4096)


def test_cgroup_v2_unlimited_is_not_a_ceiling(tmp_path, cgroup_files):
    """v2 spells "no limit" as the literal word ``max``. int() would raise; we must return None."""
    cgroup_files([_fake_cgroup(tmp_path, str(2 * 1024**3), "max")])
    assert debug.read_cgroup_memory() is None


def test_an_unlimited_v2_does_not_fall_back_to_a_stale_v1_limit(tmp_path, cgroup_files):
    """v2 is authoritative when it is there. Without the explicit ``max`` early-return, int("max")
    raises, the loop moves on, and a v1 file left behind on a hybrid host silently becomes the
    ceiling -- a limit nothing is actually enforced against."""
    (tmp_path / "v2").mkdir()
    (tmp_path / "v1").mkdir()
    v2 = _fake_cgroup(tmp_path / "v2", str(2 * 1024**3), "max")
    v1 = _fake_cgroup(tmp_path / "v1", str(2 * 1024**3), str(4 * 1024**3), v1=True)
    cgroup_files([v2, v1])

    assert debug.read_cgroup_memory() is None


def test_cgroup_v1_unlimited_sentinel_is_not_a_ceiling(tmp_path, cgroup_files):
    """v1 spells it as a huge number. Parsed naively, a 2 GB process looks like 0.00000003% of the
    limit -- a gate that can never fire, which is exactly the failure this replaces."""
    cgroup_files([_fake_cgroup(tmp_path, str(2 * 1024**3), "9223372036854771712", v1=True)])
    assert debug.read_cgroup_memory() is None


def test_no_cgroup_at_all_degrades_to_no_gate(tmp_path, cgroup_files):
    cgroup_files([(tmp_path / "nope.current", tmp_path / "nope.max")])
    assert debug.read_cgroup_memory() is None


def test_v1_is_read_when_v2_is_absent(tmp_path, cgroup_files):
    missing = (tmp_path / "absent.current", tmp_path / "absent.max")
    cgroup_files([missing, _fake_cgroup(tmp_path, str(1024**3), str(2 * 1024**3), v1=True)])
    usage_mb, limit_mb = debug.read_cgroup_memory()
    assert (usage_mb, limit_mb) == (pytest.approx(1024), pytest.approx(2048))


def test_readiness_trips_when_the_container_is_nearly_full(tmp_path, cgroup_files, monkeypatch):
    cgroup_files([_fake_cgroup(tmp_path, str(int(3.8 * 1024**3)), str(4 * 1024**3))])
    monkeypatch.setattr(debug, "READINESS_UNHEALTHY_CGROUP_PERCENT", 90)

    ready, reasons, metrics = debug.evaluate_runtime_readiness({})

    assert not ready
    assert any("cgroup_percent" in r for r in reasons), reasons
    assert metrics["cgroup_percent"] == pytest.approx(95.0, abs=0.1)
    assert metrics["cgroup_limit_mb"] == pytest.approx(4096)


def test_readiness_is_calm_below_the_threshold(tmp_path, cgroup_files, monkeypatch):
    cgroup_files([_fake_cgroup(tmp_path, str(2 * 1024**3), str(4 * 1024**3))])
    monkeypatch.setattr(debug, "READINESS_UNHEALTHY_CGROUP_PERCENT", 90)

    ready, reasons, metrics = debug.evaluate_runtime_readiness({})

    assert ready, reasons
    assert metrics["cgroup_percent"] == pytest.approx(50.0)


def test_the_gate_is_off_when_the_percent_is_zero(tmp_path, cgroup_files, monkeypatch):
    """0 disables it, like every other threshold in this file -- but the numbers still get
    reported, so an operator can size the threshold before turning it on."""
    cgroup_files([_fake_cgroup(tmp_path, str(4 * 1024**3), str(4 * 1024**3))])
    monkeypatch.setattr(debug, "READINESS_UNHEALTHY_CGROUP_PERCENT", 0)

    ready, reasons, metrics = debug.evaluate_runtime_readiness({})

    assert ready, reasons
    assert metrics["cgroup_percent"] == pytest.approx(100.0)


def test_an_unlimited_cgroup_reports_nothing_rather_than_a_fake_percentage(tmp_path, cgroup_files, monkeypatch):
    cgroup_files([_fake_cgroup(tmp_path, str(2 * 1024**3), "max")])
    monkeypatch.setattr(debug, "READINESS_UNHEALTHY_CGROUP_PERCENT", 90)

    ready, _, metrics = debug.evaluate_runtime_readiness({})

    assert ready
    assert "cgroup_percent" not in metrics
