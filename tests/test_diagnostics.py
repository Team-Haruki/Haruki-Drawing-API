import logging
import sys
from types import SimpleNamespace

from src.core import diagnostics


def test_dump_runtime_diagnostics_logs_reason(caplog, monkeypatch):
    monkeypatch.setattr(diagnostics, "_safe_snapshot_metrics", lambda: {"pid": 123, "inflight": 2})
    monkeypatch.setattr(diagnostics, "_dump_asyncio_tasks", lambda reason: None)
    monkeypatch.setattr(diagnostics, "_dump_thread_frames", lambda reason: None)

    with caplog.at_level(logging.WARNING):
        diagnostics.dump_runtime_diagnostics("unit-test")

    messages = [record.message for record in caplog.records]
    assert any("runtime diagnostics begin: reason=unit-test" in message for message in messages)
    assert any("runtime diagnostics end: reason=unit-test" in message for message in messages)


def test_signal_name_and_safe_metrics_cover_success_and_failure(monkeypatch):
    assert diagnostics._signal_name(15) == "SIGTERM"
    assert diagnostics._signal_name(999999) == "SIG999999"
    monkeypatch.setattr("src.core.debug.snapshot_process_metrics", lambda **_kwargs: {"rss": 1})
    assert diagnostics._safe_snapshot_metrics() == {"rss": 1}

    def fail(**_kwargs):
        raise RuntimeError("snapshot unavailable")

    monkeypatch.setattr("src.core.debug.snapshot_process_metrics", fail)
    assert diagnostics._safe_snapshot_metrics() == {"snapshot_error": "snapshot unavailable"}


def test_thread_dump_logs_available_and_missing_stacks(caplog, monkeypatch):
    frame = sys._getframe()
    threads = [
        SimpleNamespace(name="with-stack", ident=1, daemon=False, is_alive=lambda: True),
        SimpleNamespace(name="without-stack", ident=2, daemon=True, is_alive=lambda: False),
    ]
    monkeypatch.setattr(diagnostics.threading, "enumerate", lambda: threads)
    monkeypatch.setattr(diagnostics.sys, "_current_frames", lambda: {1: frame})
    with caplog.at_level(logging.WARNING):
        diagnostics._dump_thread_frames("coverage")
    messages = [record.message for record in caplog.records]
    assert any("name=with-stack" in message and "stack=" not in message for message in messages)
    assert any("name=without-stack stack=unavailable" in message for message in messages)
    assert any("thread stack: reason=coverage name=with-stack" in message for message in messages)


def test_asyncio_dump_covers_no_loop_empty_and_frame_stacks(caplog, monkeypatch):
    def no_loop():
        raise RuntimeError("no loop")

    monkeypatch.setattr(diagnostics.asyncio, "get_running_loop", no_loop)
    with caplog.at_level(logging.WARNING):
        diagnostics._dump_asyncio_tasks("no-loop")
    assert any("no-running-loop" in record.message for record in caplog.records)

    frame = sys._getframe()

    class FakeTask:
        def __init__(self, name, stack):
            self._name = name
            self._stack = stack

        def get_coro(self):
            return SimpleNamespace(__qualname__="fake.coro")

        def get_name(self):
            return self._name

        def done(self):
            return False

        def cancelled(self):
            return False

        def get_stack(self, limit=64):
            assert limit == 64
            return self._stack

    loop = object()
    monkeypatch.setattr(diagnostics.asyncio, "get_running_loop", lambda: loop)
    monkeypatch.setattr(
        diagnostics.asyncio,
        "all_tasks",
        lambda current: {FakeTask("empty", []), FakeTask("stacked", [frame])} if current is loop else set(),
    )
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        diagnostics._dump_asyncio_tasks("running")
    messages = [record.message for record in caplog.records]
    assert any("name=empty stack=empty" in message for message in messages)
    assert any("asyncio task stack: reason=running name=stacked" in message for message in messages)


def test_configure_runtime_diagnostics_success_failure_and_idempotence(caplog, monkeypatch):
    diagnostics._configured = False
    enabled = []
    registered = []
    monkeypatch.setattr(diagnostics.faulthandler, "enable", lambda **kwargs: enabled.append(kwargs))
    monkeypatch.setattr(
        diagnostics.faulthandler,
        "register",
        lambda signum, **kwargs: registered.append((signum, kwargs)),
    )
    diagnostics.configure_runtime_diagnostics()
    diagnostics.configure_runtime_diagnostics()
    assert len(enabled) == 1
    assert {item[0] for item in registered} == {diagnostics.signal.SIGUSR1, diagnostics.signal.SIGTERM}

    diagnostics._configured = False
    monkeypatch.setattr(diagnostics.faulthandler, "enable", lambda **_kwargs: (_ for _ in ()).throw(OSError("bad")))
    monkeypatch.setattr(
        diagnostics.faulthandler,
        "register",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("bad")),
    )
    with caplog.at_level(logging.WARNING):
        diagnostics.configure_runtime_diagnostics()
    assert diagnostics._configured
    assert sum("failed to" in record.message for record in caplog.records) >= 3
