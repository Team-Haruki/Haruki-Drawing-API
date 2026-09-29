"""`AsyncpgRenderIndex` against `FakePgPool` (plan §9.4)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
import subprocess
import sys
import threading
from typing import Any

import pytest

from src.index import (
    ContentRow,
    IndexSchemaError,
    IndexUnavailable,
    IndexWriteFailed,
    RecordResult,
    RenderIndex,
    RequestRow,
    asyncpg_index as mod,
)
from src.index.asyncpg_index import AsyncpgRenderIndex, dsn_redacted
from src.index.sql import PREFLIGHT_CONTENT, PREFLIGHT_REQUEST, RECORD
from src.settings import IndexSettings
from tests.storage_fakes import FakePgPool, FakeRenderIndex, UndefinedColumnError, UndefinedTableError

PASSWORD = "s3cr3t-pa55"
DSN = f"postgresql://haruki:{PASSWORD}@pg.internal:5432/haruki_cloud?sslmode=disable"

EXPIRES = datetime(2026, 10, 1, tzinfo=UTC)
CONTENT = ContentRow(
    hash="ab" * 32,
    group_name="pjsk",
    cdn_path=f"pjsk/api/pjsk/honor/{'ab' * 32}.png",
    storage_backend="garage",
    media_type="image/png",
    size_bytes=1234,
    expires_at=EXPIRES,
)
REQUEST = RequestRow(
    request_key="rk-1",
    content_hash="ab" * 32,
    api_path="api/pjsk/honor",
    user_id="public",
    group_name="pjsk",
    key_version=3,
    ttl_seconds=3600,
    expires_at=EXPIRES,
)
RECORD_ARGS = (
    CONTENT.hash,
    "pjsk",
    CONTENT.cdn_path,
    1234,
    "image/png",
    EXPIRES,
    "rk-1",
    CONTENT.hash,
    "api/pjsk/honor",
    "public",
    "pjsk",
    3,
    3600,
    EXPIRES,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Factory:
    def __init__(self, pool: FakePgPool | None = None, error: Exception | None = None) -> None:
        self.pool = pool or FakePgPool()
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, dsn: str, **kwargs: Any) -> FakePgPool:
        self.calls.append((dsn, kwargs))
        await asyncio.sleep(0)  # yield so concurrent first callers really contend for the lock
        if self.error is not None:
            raise self.error
        return self.pool


def _index(factory: Factory, clock: Clock | None = None, **settings: Any) -> tuple[AsyncpgRenderIndex, Clock]:
    clock = clock or Clock()
    idx = AsyncpgRenderIndex(DSN, IndexSettings(**settings), pool_factory=factory, clock=clock)
    return idx, clock


_runner: asyncio.Runner | None = None


@pytest.fixture(autouse=True)
def _event_loop() -> Any:
    """One long-lived loop per test, like a worker's loop in production."""
    global _runner
    with asyncio.Runner() as runner:
        _runner = runner
        yield
    _runner = None


def _run(coro: Any) -> Any:
    assert _runner is not None
    return _runner.run(coro)


def _assert_no_password(caplog: pytest.LogCaptureFixture) -> None:
    for record in caplog.records:
        assert PASSWORD not in record.getMessage()


# ------------------------------------------------------------------------------------------ dsn_redacted


@pytest.mark.parametrize(
    ("dsn", "expected"),
    [
        (DSN, "haruki@pg.internal:5432/haruki_cloud"),
        ("postgres://u:p%40ss@h/db", "u@h/db"),
        ("postgresql://h:6543/db", "h:6543/db"),
        ("host=h port=5432 user=u password='p' dbname=d", "u@h:5432/d"),
        ("host=h dbname=d password=x", "h/d"),
        ("", "<empty>"),
        ("garbage", "<unparseable dsn>"),
        ("postgresql://u:p@[bad/db", "<unparseable dsn>"),
        ("postgresql://u:p@h:notaport/db", "<unparseable dsn>"),
    ],
)
def test_dsn_redacted(dsn: str, expected: str) -> None:
    assert dsn_redacted(dsn) == expected
    assert "p%40ss" not in dsn_redacted(dsn)


def test_repr_and_str_never_contain_password() -> None:
    idx, _ = _index(Factory())
    assert PASSWORD not in repr(idx)
    assert PASSWORD not in str(idx)
    assert "haruki@pg.internal:5432/haruki_cloud" in repr(idx)
    assert idx.dsn_redacted == "haruki@pg.internal:5432/haruki_cloud"


# ------------------------------------------------------------------------------------------ laziness


def test_import_does_not_import_asyncpg() -> None:
    code = "import sys, src.index, src.index.asyncpg_index; assert 'asyncpg' not in sys.modules, 'asyncpg imported'"
    subprocess.run([sys.executable, "-c", code], check=True)


def test_pool_is_created_lazily_with_settings() -> None:
    factory = Factory()
    idx, _ = _index(factory, pool_min_size=1, pool_max_size=7, connect_timeout_seconds=1.5, command_timeout_seconds=2.5)
    assert factory.calls == []
    assert isinstance(idx, RenderIndex)
    _run(idx.preflight())
    assert factory.calls == [
        (
            DSN,
            {
                "min_size": 1,
                "max_size": 7,
                "timeout": 1.5,
                "command_timeout": 2.5,
                "max_inactive_connection_lifetime": 0.0,
                "reset": mod._skip_session_reset,
            },
        ),
    ]
    assert idx.ready
    assert factory.pool.statements() == [PREFLIGHT_REQUEST, PREFLIGHT_CONTENT]
    assert factory.pool.acquire_kwargs == [{"timeout": 1.5}]
    # A second preflight while ready makes no round trip and does not recreate the pool.
    _run(idx.preflight())
    assert len(factory.calls) == 1
    assert factory.pool.round_trips == 2
    assert idx.stats["preflight_ok"] == 1


def test_default_pool_keeps_one_warm_connection_per_loop() -> None:
    factory = Factory()
    idx, _ = _index(factory)
    _run(idx.preflight())
    kwargs = factory.calls[0][1]
    assert kwargs["min_size"] == 1
    assert kwargs["max_size"] == 4
    assert kwargs["max_inactive_connection_lifetime"] == 0.0  # asyncpg's own default (300 s) is overridden
    assert kwargs["reset"] is mod._skip_session_reset


def test_pool_idle_lifetime_is_configurable_and_never_negative() -> None:
    factory = Factory()
    idx, _ = _index(factory, pool_min_size=0, pool_max_inactive_seconds=300.0)
    _run(idx.preflight())
    assert factory.calls[0][1]["min_size"] == 0
    assert factory.calls[0][1]["max_inactive_connection_lifetime"] == 300.0
    factory = Factory()
    idx, _ = _index(factory, pool_max_inactive_seconds=-1.0)
    _run(idx.preflight())
    assert factory.calls[0][1]["max_inactive_connection_lifetime"] == 0.0


def test_skip_session_reset_sends_nothing() -> None:
    class Conn:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"reset hook touched the connection: {name}")

    assert _run(mod._skip_session_reset(Conn())) is None


def test_create_pool_accepts_the_reset_hook() -> None:
    """The pinned asyncpg (>=0.30) takes `reset=` and `max_inactive_connection_lifetime=`, without connecting."""
    import inspect

    asyncpg = pytest.importorskip("asyncpg")
    parameters = inspect.signature(asyncpg.create_pool).parameters
    assert "reset" in parameters
    assert "max_inactive_connection_lifetime" in parameters


def test_default_pool_factory_imports_asyncpg_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeAsyncpg:
        @staticmethod
        async def create_pool(dsn: str, **kwargs: Any) -> str:
            captured["dsn"] = dsn
            captured.update(kwargs)
            return "pool"

    monkeypatch.setitem(sys.modules, "asyncpg", FakeAsyncpg)
    assert _run(mod._asyncpg_pool_factory("dsn", min_size=0)) == "pool"
    assert captured == {"dsn": "dsn", "min_size": 0}


# ------------------------------------------------------------------------------------------ lookup / record


def test_record_is_one_statement_without_a_transaction() -> None:
    row = {"cdn_path": "pjsk/api/pjsk/card/x.png", "media_type": None, "size_bytes": 99, "prior_backend": "garage"}
    factory = Factory(FakePgPool(rows={CONTENT.hash: row}))
    idx, _ = _index(factory)
    _run(idx.preflight())
    factory.pool.events.clear()
    result = _run(idx.record(CONTENT, REQUEST))
    assert factory.pool.events == [("fetchrow", RECORD, RECORD_ARGS)]
    assert factory.pool.round_trips == 1
    assert result.cdn_path == "pjsk/api/pjsk/card/x.png"
    assert (result.media_type, result.size_bytes, result.prior_backend) == (None, 99, "garage")
    assert result.acquire_seconds >= 0.0
    assert result.connect_seconds == 0.0  # the loop was already ready
    assert idx.stats["records"] == 1
    assert "lookups" not in idx.stats


def test_first_record_on_a_loop_reports_connect_time() -> None:
    row = {"cdn_path": "k", "media_type": "image/png", "size_bytes": 1, "prior_backend": None}
    factory = Factory(FakePgPool(default_row=row))
    idx, _ = _index(factory)
    first = _run(idx.record(CONTENT, REQUEST))
    assert first.connect_seconds > 0.0
    assert first.prior_backend is None
    assert factory.pool.statements() == [PREFLIGHT_REQUEST, PREFLIGHT_CONTENT, RECORD]
    second = _run(idx.record(CONTENT, REQUEST))
    assert second.connect_seconds == 0.0


def test_record_without_a_returned_row_falls_back_to_the_sent_values() -> None:
    idx, _ = _index(Factory())
    result = _run(idx.record(CONTENT, REQUEST))
    assert result == RecordResult(
        CONTENT.cdn_path, "image/png", 1234, None, result.acquire_seconds, result.connect_seconds
    )


def test_record_failure_raises_write_failed_without_backoff(caplog: pytest.LogCaptureFixture) -> None:
    class ForeignKeyViolationError(Exception):
        pass

    pool = FakePgPool(errors={RECORD: ForeignKeyViolationError(f"bad row for {DSN}")})
    idx, _ = _index(Factory(pool))
    with caplog.at_level(logging.WARNING, logger="src.index.asyncpg_index"):
        with pytest.raises(IndexWriteFailed):
            _run(idx.record(CONTENT, REQUEST))
    assert not [event for event in pool.events if event[0].startswith("transaction")]
    assert idx.stats["write_failures"] == 1
    # A statement-level failure is not a transport failure: no backoff window.
    assert not idx.in_backoff()
    assert idx.ready
    _assert_no_password(caplog)


def test_record_transport_failure_is_write_failed_with_backoff(caplog: pytest.LogCaptureFixture) -> None:
    class ConnectionDoesNotExistError(Exception):
        pass

    pool = FakePgPool(errors={RECORD: ConnectionDoesNotExistError("gone")})
    idx, _ = _index(Factory(pool))
    with caplog.at_level(logging.WARNING, logger="src.index.asyncpg_index"):
        with pytest.raises(IndexWriteFailed):
            _run(idx.record(CONTENT, REQUEST))
    assert idx.in_backoff()
    trips = pool.round_trips
    with pytest.raises(IndexUnavailable):
        _run(idx.record(CONTENT, REQUEST))
    assert pool.round_trips == trips
    assert idx.stats["backoff_skips"] == 1


def test_record_timeout_starts_backoff() -> None:
    pool = FakePgPool(errors={RECORD: TimeoutError()})
    idx, _ = _index(Factory(pool))
    with pytest.raises(IndexWriteFailed):
        _run(idx.record(CONTENT, REQUEST))
    assert idx.in_backoff()
    assert not idx.ready


def test_schema_error_after_preflight_is_schema_error() -> None:
    pool = FakePgPool()
    idx, _ = _index(Factory(pool), connect_retry_seconds=10)
    _run(idx.preflight())
    pool.errors[RECORD] = UndefinedColumnError("column expires_at does not exist")
    with pytest.raises(IndexSchemaError):
        _run(idx.record(CONTENT, REQUEST))
    with pytest.raises(IndexSchemaError):
        _run(idx.record(CONTENT, REQUEST))
    assert idx.stats["backoff_skips"] == 1


# ------------------------------------------------------------------------------------------ preflight failures


@pytest.mark.parametrize("error_type", [UndefinedTableError, UndefinedColumnError])
def test_preflight_schema_missing_logs_once_and_backs_off(
    error_type: type[Exception], caplog: pytest.LogCaptureFixture
) -> None:
    pool = FakePgPool(errors={PREFLIGHT_CONTENT: error_type(f"relation missing ({DSN})")})
    idx, clock = _index(Factory(pool), connect_retry_seconds=30)
    with caplog.at_level(logging.WARNING, logger="src.index.asyncpg_index"):
        with pytest.raises(IndexSchemaError):
            _run(idx.preflight())
        trips = pool.round_trips
        assert trips == 2
        # Inside the backoff window: no round trip at all, same error class.
        for _ in range(3):
            with pytest.raises(IndexSchemaError):
                _run(idx.record(CONTENT, REQUEST))
        assert pool.round_trips == trips
        assert idx.stats["backoff_skips"] == 3
        # After the window, preflight is retried; still missing -> no second ERROR.
        clock.now += 31
        with pytest.raises(IndexSchemaError):
            _run(idx.preflight())
        assert pool.round_trips == trips * 2
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "Cloud DDL not shipped" in errors[0].getMessage()
    assert idx.stats["schema_missing"] == 2
    _assert_no_password(caplog)
    # The schema ships: the next window succeeds.
    del pool.errors[PREFLIGHT_CONTENT]
    clock.now += 31
    _run(idx.preflight())
    assert idx.ready


@pytest.mark.parametrize(
    "error",
    [OSError("connection refused"), TimeoutError(), type("CannotConnectNowError", (Exception,), {})("starting")],
)
def test_connect_failure_is_unavailable_with_backoff(error: Exception, caplog: pytest.LogCaptureFixture) -> None:
    factory = Factory(error=error)
    idx, clock = _index(factory, connect_retry_seconds=5)
    with caplog.at_level(logging.WARNING, logger="src.index.asyncpg_index"):
        with pytest.raises(IndexUnavailable):
            _run(idx.preflight())
        with pytest.raises(IndexUnavailable):
            _run(idx.record(CONTENT, REQUEST))
    assert len(factory.calls) == 1
    assert idx.stats["unavailable"] == 1
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]
    _assert_no_password(caplog)
    clock.now += 6
    factory.error = None
    assert isinstance(_run(idx.record(CONTENT, REQUEST)), RecordResult)
    assert len(factory.calls) == 2


def test_schema_error_raised_by_pool_factory() -> None:
    idx, _ = _index(Factory(error=UndefinedTableError("nope")))
    with pytest.raises(IndexSchemaError):
        _run(idx.preflight())


def test_preflight_transport_error_on_acquire() -> None:
    pool = FakePgPool(acquire_error=type("InterfaceError", (Exception,), {})("pool closing"))
    idx, _ = _index(Factory(pool))
    with pytest.raises(IndexUnavailable):
        _run(idx.preflight())
    assert idx.in_backoff()


def test_password_scrubbed_from_exception_text_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    keyword_dsn = f"host=h user=u password={PASSWORD} dbname=d"
    factory = Factory(error=OSError(f"could not connect using password {PASSWORD} dsn {keyword_dsn}"))
    idx = AsyncpgRenderIndex(keyword_dsn, IndexSettings(), pool_factory=factory, clock=Clock())
    with caplog.at_level(logging.WARNING, logger="src.index.asyncpg_index"):
        with pytest.raises(IndexUnavailable):
            _run(idx.preflight())
    assert caplog.records
    _assert_no_password(caplog)
    assert PASSWORD not in repr(idx)


def test_unparseable_url_dsn_scrub_does_not_raise(caplog: pytest.LogCaptureFixture) -> None:
    bad = "postgresql://u:p@h:notaport/db"
    idx = AsyncpgRenderIndex(bad, IndexSettings(), pool_factory=Factory(error=OSError("x")), clock=Clock())
    with pytest.raises(IndexUnavailable):
        _run(idx.preflight())
    assert "<unparseable dsn>" in repr(idx)


@pytest.mark.parametrize("dsn", ["", "postgresql://u:p@[bad/db"])
def test_degenerate_dsn_logs_without_raising(dsn: str, caplog: pytest.LogCaptureFixture) -> None:
    idx = AsyncpgRenderIndex(dsn, IndexSettings(), pool_factory=Factory(error=OSError("x")), clock=Clock())
    with caplog.at_level(logging.WARNING, logger="src.index.asyncpg_index"):
        with pytest.raises(IndexUnavailable):
            _run(idx.preflight())
    assert caplog.records


def test_concurrent_first_calls_preflight_once() -> None:
    factory = Factory()
    idx, _ = _index(factory)

    async def main() -> None:
        await asyncio.gather(*(idx.record(CONTENT, REQUEST) for _ in range(8)))

    _run(main())
    assert len(factory.calls) == 1
    assert idx.stats["preflight_ok"] == 1


# ------------------------------------------------------------------------------------------ event loops


def _run_in_thread_loop(coro_factory: Any) -> Any:
    result: dict[str, Any] = {}

    def target() -> None:
        try:
            result["value"] = asyncio.run(coro_factory())
        except BaseException as exc:  # surfaced to the test thread
            result["error"] = exc

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


class LoopBoundPool(FakePgPool):
    """Fails like asyncpg when used from a loop other than the one that created it."""

    def __init__(self) -> None:
        super().__init__()
        self.loop = asyncio.get_running_loop()

    def acquire(self, **kwargs: Any) -> Any:
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("got Future attached to a different loop")
        return super().acquire(**kwargs)


def test_each_event_loop_gets_its_own_pool_and_preflight() -> None:
    pools: list[LoopBoundPool] = []

    async def factory(dsn: str, **kwargs: Any) -> LoopBoundPool:
        pool = LoopBoundPool()
        pools.append(pool)
        return pool

    idx = AsyncpgRenderIndex(DSN, IndexSettings(), pool_factory=factory, clock=Clock())
    _run(idx.preflight())  # the lifespan loop
    assert isinstance(_run_in_thread_loop(lambda: idx.record(CONTENT, REQUEST)), RecordResult)  # a worker loop
    assert isinstance(_run(idx.record(CONTENT, REQUEST)), RecordResult)  # back on the first loop: pool reused
    assert len(pools) == 2
    assert pools[0].loop is not pools[1].loop
    assert idx.stats["pool_created"] == 2
    assert idx.stats["preflight_ok"] == 2
    assert idx.ready


def test_backoff_is_shared_across_loops() -> None:
    pool = FakePgPool(errors={RECORD: TimeoutError()})
    idx, _ = _index(Factory(pool), connect_retry_seconds=30)
    with pytest.raises(IndexUnavailable):
        _run(idx.record(CONTENT, REQUEST))
    trips = pool.round_trips
    with pytest.raises(IndexUnavailable):
        _run_in_thread_loop(lambda: idx.record(CONTENT, REQUEST))
    assert pool.round_trips == trips
    assert idx.stats["backoff_skips"] == 1
    assert not idx.ready


def test_close_hands_foreign_running_loop_pools_to_their_loop() -> None:
    closed = threading.Event()
    started = threading.Event()
    release = threading.Event()
    holder: dict[str, Any] = {}

    class ClosingPool(FakePgPool):
        async def close(self) -> None:
            await super().close()
            closed.set()

    async def factory(dsn: str, **kwargs: Any) -> ClosingPool:
        return ClosingPool()

    idx = AsyncpgRenderIndex(DSN, IndexSettings(), pool_factory=factory, clock=Clock())

    def worker() -> None:
        loop = asyncio.new_event_loop()
        holder["loop"] = loop

        async def main() -> None:
            await idx.preflight()
            started.set()
            await asyncio.to_thread(release.wait)

        loop.run_until_complete(main())
        loop.close()

    thread = threading.Thread(target=worker)
    thread.start()
    assert started.wait(5)
    _run(idx.close())
    assert closed.wait(5)
    release.set()
    thread.join()
    assert not idx.ready


def test_close_terminates_pools_of_stopped_loops() -> None:
    terminated: list[bool] = []

    class TerminablePool(FakePgPool):
        def terminate(self) -> None:
            terminated.append(True)

    async def factory(dsn: str, **kwargs: Any) -> TerminablePool:
        return TerminablePool()

    idx = AsyncpgRenderIndex(DSN, IndexSettings(), pool_factory=factory, clock=Clock())
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(idx.preflight())
        _run(idx.close())  # the other loop exists but is not running
    finally:
        loop.close()
    assert terminated == [True]


# ------------------------------------------------------------------------------------------ close


def test_close_is_idempotent_and_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    pool = FakePgPool(close_error=OSError("already closed"))
    idx, _ = _index(Factory(pool))
    _run(idx.close())  # no pool yet
    idx2, _ = _index(Factory(pool))
    _run(idx2.preflight())
    with caplog.at_level(logging.WARNING, logger="src.index.asyncpg_index"):
        _run(idx2.close())
        _run(idx2.close())
    assert pool.closed
    assert not idx2.ready
    with pytest.raises(IndexUnavailable):
        _run(idx2.record(CONTENT, REQUEST))
    _assert_no_password(caplog)


# ------------------------------------------------------------------------------------------ FakeRenderIndex


def test_fake_render_index_implements_protocol() -> None:
    legacy = ContentRow(CONTENT.hash, "pjsk", "pjsk/legacy.png", "legacy_disk", None, None, None)
    fake = FakeRenderIndex({CONTENT.hash: legacy})
    assert isinstance(fake, RenderIndex)
    _run(fake.preflight())
    upgraded = _run(fake.record(CONTENT, REQUEST))
    assert upgraded == RecordResult(CONTENT.cdn_path, "image/png", 1234, "legacy_disk")
    assert fake.content[CONTENT.hash] == CONTENT
    other = ContentRow(CONTENT.hash, "pjsk", "pjsk/other.png", "garage", None, None, None)
    kept = _run(fake.record(other, REQUEST))
    assert kept == RecordResult(CONTENT.cdn_path, "image/png", 1234, "garage")  # a garage row keeps its path
    assert fake.content[CONTENT.hash] == CONTENT
    assert fake.requests["rk-1"] == REQUEST
    _run(fake.close())
    assert fake.closed
    assert [name for name, _ in fake.calls] == ["preflight", "record", "record", "close"]

    failing = FakeRenderIndex(preflight_error=IndexSchemaError("x"), record_error=IndexWriteFailed("z"))
    with pytest.raises(IndexSchemaError):
        _run(failing.preflight())
    with pytest.raises(IndexWriteFailed):
        _run(failing.record(CONTENT, REQUEST))
