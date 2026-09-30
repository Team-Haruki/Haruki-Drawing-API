"""`RECORD` against a real PostgreSQL: the one-statement write leaves the rows the two-statement transaction left.

Opt-in: set `HARUKI_TEST_PG_DSN` to a throwaway database (the test creates and drops two schemas in it), e.g.
`postgresql://bench:bench@127.0.0.1:55432/haruki_cloud`. Without it every test here is skipped.

Each scenario runs the same inputs through the former `BEGIN; UPSERT_CONTENT; UPSERT_REQUEST; COMMIT` in one
schema, through `RECORD` in another and through the writer's `PREPARE_UPLOAD` + `RECORD_UPLOAD` in a third, all
built from Cloud's canonical DDL, then compares every column of both tables. Timestamps are compared as "set to
this write's now()" versus "kept", since the writes run at different instants.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import os
import secrets
from typing import Any

import pytest

from src.index.sql import (
    PREFLIGHT_CONTENT,
    PREFLIGHT_REQUEST,
    PREPARE_UPLOAD,
    RECORD,
    RECORD_UPLOAD,
    UPSERT_CONTENT as FORMER_UPSERT_CONTENT,
    UPSERT_REQUEST as FORMER_UPSERT_REQUEST,
)

DSN = os.environ.get("HARUKI_TEST_PG_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="HARUKI_TEST_PG_DSN not set")

# Cloud's initSQL + renderIndexDDL (utils/imagecache/pgstore.go, pgstore_ddl.go), the tables Drawing writes into.
CLOUD_DDL = (
    """CREATE TABLE image_cache_entries (
        hash TEXT PRIMARY KEY, group_name TEXT NOT NULL, cdn_path TEXT NOT NULL, file_path TEXT NOT NULL,
        size_bytes BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    "ALTER TABLE image_cache_entries ADD COLUMN IF NOT EXISTS storage_backend TEXT",
    "ALTER TABLE image_cache_entries ADD COLUMN IF NOT EXISTS media_type TEXT",
    "ALTER TABLE image_cache_entries ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ NULL",
    "ALTER TABLE image_cache_entries ADD COLUMN IF NOT EXISTS last_referenced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    "ALTER TABLE image_cache_entries ALTER COLUMN file_path DROP NOT NULL",
    "ALTER TABLE image_cache_entries ADD COLUMN writer_node TEXT",
    "ALTER TABLE image_cache_entries ADD COLUMN written_at TIMESTAMPTZ",
    """CREATE TABLE render_cache_index (
        request_key TEXT PRIMARY KEY, content_hash TEXT NOT NULL REFERENCES image_cache_entries(hash),
        api_path TEXT NOT NULL, user_id TEXT NOT NULL DEFAULT 'public', group_name TEXT NOT NULL DEFAULT 'pjsk',
        key_version INT NOT NULL DEFAULT 3, ttl_seconds BIGINT NOT NULL DEFAULT 0, expires_at TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), last_used_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    """CREATE TABLE image_cache_object_deletions (
        content_hash TEXT NOT NULL, cdn_path TEXT NOT NULL, queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        attempts INT NOT NULL DEFAULT 0, next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (content_hash, cdn_path))""",
)
CONTENT_TIMESTAMPS = ("created_at", "last_referenced_at")
REQUEST_TIMESTAMPS = ("created_at", "last_used_at")
T0 = datetime(2026, 10, 1, tzinfo=UTC)


def _hash() -> str:
    return secrets.token_hex(32)


def _args(
    content_hash: str,
    request_key: str,
    *,
    api_path: str | None = "api/pjsk/honor",
    expires: datetime | None = T0,
    ttl: int = 3600,
    size: int = 1234,
) -> tuple[Any, ...]:
    cdn_path = f"pjsk/{api_path}/{content_hash}.png"
    content = (content_hash, "pjsk", cdn_path, size, "image/png", expires, "node-a", T0)
    request = (request_key, content_hash, api_path, "public", "pjsk", 3, ttl, expires)
    return content + request


async def _connect() -> Any:
    asyncpg = pytest.importorskip("asyncpg")
    return await asyncpg.connect(DSN)


async def _schema(conn: Any, name: str) -> None:
    await conn.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE; CREATE SCHEMA {name}")
    await conn.execute(f"SET search_path TO {name}")
    for statement in CLOUD_DDL:
        await conn.execute(statement)


async def _write_former(conn: Any, args: tuple[Any, ...]) -> datetime:
    async with conn.transaction():
        now = await conn.fetchval("SELECT now()")
        await conn.execute(FORMER_UPSERT_CONTENT, *args[:8])
        await conn.execute(FORMER_UPSERT_REQUEST, *args[8:])
    return now


async def _write_record(conn: Any, args: tuple[Any, ...]) -> tuple[datetime, Any]:
    # The explicit transaction only captures now(); RECORD is one statement either way.
    async with conn.transaction():
        now = await conn.fetchval("SELECT now()")
        row = await conn.fetchrow(RECORD, *args)
    return now, row


async def _write_gated(conn: Any, args: tuple[Any, ...]) -> tuple[datetime, Any]:
    # The writer's path: PREPARE_UPLOAD commits the candidate's intent (on a miss), the PUT would run here, then
    # RECORD_UPLOAD under the lock. A hit registers no intent and passes uploaded=false, as the service does.
    existing = await conn.fetchval(PREPARE_UPLOAD, args[0], args[2])
    async with conn.transaction():
        now = await conn.fetchval("SELECT now()")
        row = await conn.fetchrow(RECORD_UPLOAD, *args, existing is None)
    return now, row


def _normalise(rows: list[Any], timestamps: tuple[str, ...], stamps: dict[datetime, int]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        values = dict(row)
        for column in timestamps:
            values[column] = f"write#{stamps[values[column]]}" if values[column] in stamps else "default"
        out.append(values)
    return out


async def _snapshot(conn: Any, stamps: dict[datetime, int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    content = await conn.fetch("SELECT * FROM image_cache_entries ORDER BY hash")
    requests = await conn.fetch("SELECT * FROM render_cache_index ORDER BY request_key")
    return _normalise(content, CONTENT_TIMESTAMPS, stamps), _normalise(requests, REQUEST_TIMESTAMPS, stamps)


def test_record_rows_match_the_former_two_statement_transaction() -> None:
    async def scenario() -> None:
        h1, h2, h3 = _hash(), _hash(), _hash()
        steps: list[tuple[str, tuple[Any, ...]]] = [
            ("new hash, new key", _args(h1, "k1")),
            ("same bytes from another endpoint", _args(h1, "k2", api_path="api/pjsk/card", expires=T0 + timedelta(1))),
            ("older expiry never shortens the content row", _args(h1, "k3", expires=T0 - timedelta(1))),
            ("request key re-pointed at new bytes", _args(h2, "k2", ttl=60, size=77)),
            ("infinite ttl makes the content row infinite", _args(h1, "k4", expires=None, ttl=0)),
            ("finite after infinite stays infinite", _args(h1, "k5", expires=T0 + timedelta(9))),
            ("legacy_disk row is upgraded", _args(h3, "k6")),
        ]
        former, record, gated = await _connect(), await _connect(), await _connect()
        try:
            await _schema(former, "drawing_record_former")
            await _schema(record, "drawing_record_single")
            await _schema(gated, "drawing_record_gated")
            legacy = (
                "INSERT INTO image_cache_entries (hash, group_name, cdn_path, file_path, size_bytes, storage_backend,"
                " media_type) VALUES ($1, 'pjsk', $2, '/cache/x.png', 5, 'legacy_disk', 'image/png')"
            )
            for conn in (former, record, gated):
                await conn.execute(legacy, h3, f"pjsk/{h3}.png")
            former_stamps: dict[datetime, int] = {}
            record_stamps: dict[datetime, int] = {}
            gated_stamps: dict[datetime, int] = {}
            returned = []
            for index, (label, args) in enumerate(steps):
                former_stamps[await _write_former(former, args)] = index
                now, row = await _write_record(record, args)
                record_stamps[now] = index
                returned.append((label, dict(row)))
                assert await _snapshot(record, record_stamps) == await _snapshot(former, former_stamps), label
                now, gated_row = await _write_gated(gated, args)
                gated_stamps[now] = index
                assert {key: gated_row[key] for key in row.keys()} == dict(row), label
                assert await _snapshot(gated, gated_stamps) == await _snapshot(former, former_stamps), label
                # Every recorded candidate's intent is gone; no hit ever queued one.
                assert await gated.fetchval("SELECT count(*) FROM image_cache_object_deletions") == 0, label
            prior = [(label, row["prior_backend"], row["cdn_path"]) for label, row in returned]
            assert prior == [
                ("new hash, new key", None, f"pjsk/api/pjsk/honor/{h1}.png"),
                ("same bytes from another endpoint", "garage", f"pjsk/api/pjsk/honor/{h1}.png"),
                ("older expiry never shortens the content row", "garage", f"pjsk/api/pjsk/honor/{h1}.png"),
                ("request key re-pointed at new bytes", None, f"pjsk/api/pjsk/honor/{h2}.png"),
                ("infinite ttl makes the content row infinite", "garage", f"pjsk/api/pjsk/honor/{h1}.png"),
                ("finite after infinite stays infinite", "garage", f"pjsk/api/pjsk/honor/{h1}.png"),
                ("legacy_disk row is upgraded", "legacy_disk", f"pjsk/api/pjsk/honor/{h3}.png"),
            ]
            assert returned[3][1]["size_bytes"] == 77
            # Cloud's LookupRender join still resolves every key to the stored path.
            lookup = (
                "SELECT r.content_hash, e.cdn_path, e.storage_backend FROM render_cache_index r "
                "JOIN image_cache_entries e ON e.hash = r.content_hash WHERE r.request_key = $1"
            )
            assert tuple(await record.fetchrow(lookup, "k2")) == (h2, f"pjsk/api/pjsk/honor/{h2}.png", "garage")
            assert tuple(await record.fetchrow(lookup, "k6")) == (h3, f"pjsk/api/pjsk/honor/{h3}.png", "garage")
        finally:
            for conn, name in (
                (former, "drawing_record_former"),
                (record, "drawing_record_single"),
                (gated, "drawing_record_gated"),
            ):
                await conn.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
                await conn.close()

    asyncio.run(scenario())


def test_record_is_atomic_and_preflight_matches_the_schema() -> None:
    async def scenario() -> None:
        conn = await _connect()
        try:
            await _schema(conn, "drawing_record_atomic")
            await conn.execute(PREFLIGHT_REQUEST)
            await conn.execute(PREFLIGHT_CONTENT)
            h1 = _hash()
            with pytest.raises(Exception, match="null value"):
                await conn.fetchrow(RECORD, *_args(h1, "k1", api_path=None))  # the request insert fails
            assert await conn.fetchval("SELECT count(*) FROM image_cache_entries") == 0  # so no content row
            assert await conn.fetchval("SELECT count(*) FROM render_cache_index") == 0
            row = await conn.fetchrow(RECORD, *_args(h1, "k1"))
            assert row["prior_backend"] is None
            assert await conn.fetchval("SELECT count(*) FROM render_cache_index WHERE content_hash = $1", h1) == 1
        finally:
            await conn.execute("DROP SCHEMA IF EXISTS drawing_record_atomic CASCADE")
            await conn.close()

    asyncio.run(scenario())
