import logging

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

