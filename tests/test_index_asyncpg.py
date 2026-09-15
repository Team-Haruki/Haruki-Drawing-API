"""`AsyncpgRenderIndex` against `FakePgPool` (plan §9.4)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
import subprocess
import sys
from typing import Any

import pytest

from src.index import (
    ContentRow,
    IndexSchemaError,
    IndexUnavailable,
    IndexWriteFailed,
    RenderIndex,
    RequestRow,
    asyncpg_index as mod,
)
from src.index.asyncpg_index import AsyncpgRenderIndex, dsn_redacted
from src.index.sql import PREFLIGHT_CONTENT, PREFLIGHT_REQUEST, SELECT_CONTENT, UPSERT_CONTENT, UPSERT_REQUEST
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


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


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
        (DSN, {"min_size": 1, "max_size": 7, "timeout": 1.5, "command_timeout": 2.5}),
    ]
    assert idx.ready
    assert factory.pool.statements() == [PREFLIGHT_REQUEST, PREFLIGHT_CONTENT]
    assert factory.pool.acquire_kwargs == [{"timeout": 1.5}]
    # A second preflight while ready makes no round trip and does not recreate the pool.
    _run(idx.preflight())
    assert len(factory.calls) == 1
    assert factory.pool.round_trips == 2
    assert idx.stats["preflight_ok"] == 1


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


def test_lookup_content_hit_and_miss() -> None:
    row = {
        "hash": CONTENT.hash,
        "group_name": "pjsk",
        "cdn_path": CONTENT.cdn_path,
        "storage_backend": "garage",
        "media_type": None,
        "size_bytes": None,
        "expires_at": None,
    }
    factory = Factory(FakePgPool(rows={CONTENT.hash: row}))
    idx, _ = _index(factory)
    got = _run(idx.lookup_content(CONTENT.hash))
    assert got == ContentRow(CONTENT.hash, "pjsk", CONTENT.cdn_path, "garage", None, None, None)
    assert _run(idx.lookup_content("cd" * 32)) is None
    selects = [event for event in factory.pool.events if event[1] == SELECT_CONTENT]
    assert [event[2] for event in selects] == [(CONTENT.hash,), ("cd" * 32,)]
    assert idx.stats["lookups"] == 2


def test_record_is_two_statements_in_one_transaction() -> None:
    factory = Factory()
    idx, _ = _index(factory)
    _run(idx.preflight())
    factory.pool.events.clear()
    _run(idx.record(CONTENT, REQUEST))
    assert factory.pool.events == [
        ("transaction.begin", None, ()),
        (
            "execute",
            UPSERT_CONTENT,
            (CONTENT.hash, "pjsk", CONTENT.cdn_path, 1234, "image/png", EXPIRES),
        ),
        (
            "execute",
            UPSERT_REQUEST,
            ("rk-1", CONTENT.hash, "api/pjsk/honor", "public", "pjsk", 3, 3600, EXPIRES),
        ),
        ("transaction.commit", None, ()),
    ]
    assert idx.stats["records"] == 1


def test_record_failure_rolls_back_and_raises_write_failed(caplog: pytest.LogCaptureFixture) -> None:
    class ForeignKeyViolationError(Exception):
        pass

    pool = FakePgPool(errors={UPSERT_REQUEST: ForeignKeyViolationError(f"bad row for {DSN}")})
    idx, _ = _index(Factory(pool))
    with caplog.at_level(logging.WARNING, logger="src.index.asyncpg_index"):
        with pytest.raises(IndexWriteFailed):
            _run(idx.record(CONTENT, REQUEST))
    assert pool.events[-1] == ("transaction.rollback", None, ())
    assert idx.stats["write_failures"] == 1
    # A statement-level failure is not a transport failure: no backoff window.
    assert not idx.in_backoff()
    assert idx.ready
    _assert_no_password(caplog)


def test_record_transport_failure_is_write_failed_with_backoff(caplog: pytest.LogCaptureFixture) -> None:
    class ConnectionDoesNotExistError(Exception):
        pass

    pool = FakePgPool(errors={UPSERT_CONTENT: ConnectionDoesNotExistError("gone")})
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


def test_lookup_non_transport_failure_is_unavailable_without_backoff() -> None:
    pool = FakePgPool(errors={SELECT_CONTENT: RuntimeError("boom")})
    idx, _ = _index(Factory(pool))
    with pytest.raises(IndexUnavailable) as info:
        _run(idx.lookup_content("x"))
    assert not isinstance(info.value, IndexWriteFailed)
    assert not idx.in_backoff()


def test_lookup_timeout_starts_backoff() -> None:
    pool = FakePgPool(errors={SELECT_CONTENT: TimeoutError()})
    idx, _ = _index(Factory(pool))
    with pytest.raises(IndexUnavailable):
        _run(idx.lookup_content("x"))
    assert idx.in_backoff()
    assert not idx.ready


def test_schema_error_after_preflight_is_schema_error() -> None:
    pool = FakePgPool()
    idx, _ = _index(Factory(pool), connect_retry_seconds=10)
    _run(idx.preflight())
    pool.errors[SELECT_CONTENT] = UndefinedColumnError("column expires_at does not exist")
    with pytest.raises(IndexSchemaError):
        _run(idx.lookup_content("x"))
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
                _run(idx.lookup_content("x"))
            with pytest.raises(IndexSchemaError):
                _run(idx.record(CONTENT, REQUEST))
        assert pool.round_trips == trips
        assert idx.stats["backoff_skips"] == 6
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
            _run(idx.lookup_content("x"))
    assert len(factory.calls) == 1
    assert idx.stats["unavailable"] == 1
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]
    _assert_no_password(caplog)
    clock.now += 6
    factory.error = None
    assert _run(idx.lookup_content("x")) is None
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
        await asyncio.gather(*(idx.lookup_content("x") for _ in range(8)))

    _run(main())
    assert len(factory.calls) == 1
    assert idx.stats["preflight_ok"] == 1


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
        _run(idx2.lookup_content("x"))
    _assert_no_password(caplog)


# ------------------------------------------------------------------------------------------ FakeRenderIndex


def test_fake_render_index_implements_protocol() -> None:
    legacy = ContentRow(CONTENT.hash, "pjsk", "pjsk/legacy.png", "legacy_disk", None, None, None)
    fake = FakeRenderIndex({CONTENT.hash: legacy})
    assert isinstance(fake, RenderIndex)
    _run(fake.preflight())
    assert _run(fake.lookup_content(CONTENT.hash)) == legacy
    _run(fake.record(CONTENT, REQUEST))
    assert fake.content[CONTENT.hash] == CONTENT
    other = ContentRow(CONTENT.hash, "pjsk", "pjsk/other.png", "garage", None, None, None)
    _run(fake.record(other, REQUEST))
    assert fake.content[CONTENT.hash] == CONTENT  # a garage row keeps its path
    assert fake.requests["rk-1"] == REQUEST
    _run(fake.close())
    assert fake.closed
    assert [name for name, _ in fake.calls] == ["preflight", "lookup_content", "record", "record", "close"]

    failing = FakeRenderIndex(
        preflight_error=IndexSchemaError("x"), lookup_error=IndexUnavailable("y"), record_error=IndexWriteFailed("z")
    )
    with pytest.raises(IndexSchemaError):
        _run(failing.preflight())
    with pytest.raises(IndexUnavailable):
        _run(failing.lookup_content("h"))
    with pytest.raises(IndexWriteFailed):
        _run(failing.record(CONTENT, REQUEST))
