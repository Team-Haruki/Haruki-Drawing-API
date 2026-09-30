"""Optional loopback-only PostgreSQL verification, using a new schema per test.

HARUKI_TEST_INDEX_DSN=postgresql://postgres@127.0.0.1:55439/postgres?sslmode=disable
The test creates and drops only its random drawing_test_* schema.

Cloud's GC is replayed with its own statements (utils/imagecache/gc.go `deletePending`/`collectEntry`,
pgstore_outbox.go), against the fake object store the writer uploads to, so the races below are the real ones:
the writer's PUT runs with no lock and no transaction, and the locked write must still never record a path GC
may delete, nor leave an uploaded object that is neither recorded nor queued.
"""

import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import random
import struct
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest

from src.index.asyncpg_index import AsyncpgRenderIndex
from src.index.sql import LOCK_CONTENT
from src.settings import IndexSettings
from tests.storage_fakes import FakeObjectStore
from tests.test_artifact_service import DIGEST, _directive, _payload, _service

DSN = os.environ.get("HARUKI_TEST_INDEX_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="HARUKI_TEST_INDEX_DSN not configured")

# Cloud's statements, verbatim.
CLOUD_PENDING_OBJECT = (
    "SELECT 1 FROM image_cache_object_deletions WHERE content_hash = $1 AND cdn_path = $2 "
    "AND next_attempt_at <= clock_timestamp() FOR UPDATE"
)
CLOUD_LIVE_ENTRY_PATHS = "SELECT hash, cdn_path FROM image_cache_entries WHERE hash = ANY($1)"
CLOUD_FINISH_OBJECT_DELETE = "DELETE FROM image_cache_object_deletions WHERE content_hash = $1 AND cdn_path = $2"
CLOUD_RETIRE_OBJECT = """WITH retired AS (
 DELETE FROM image_cache_entries e WHERE e.hash = $1 AND e.cdn_path = $3
 AND e.storage_backend = 'garage' AND e.last_referenced_at < $2
 AND NOT EXISTS (SELECT 1 FROM render_cache_index r WHERE r.content_hash = e.hash)
 RETURNING hash, cdn_path
), queued AS (
 INSERT INTO image_cache_object_deletions (content_hash, cdn_path)
 SELECT hash, cdn_path FROM retired ON CONFLICT (content_hash, cdn_path) DO NOTHING
) SELECT count(*) FROM retired"""


@asynccontextmanager
async def database(dsn: str = ""):
    import asyncpg

    assert urlsplit(DSN).hostname in {"127.0.0.1", "localhost", "::1"}, "integration DSN must be local"
    schema = "drawing_test_" + uuid4().hex
    conn = await asyncpg.connect(DSN)
    index = None
    try:
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        await conn.execute(f'SET search_path TO "{schema}"')
        await conn.execute((Path(__file__).parent / "fixtures/render_index_schema.sql").read_text())

        async def factory(dsn, **kwargs):
            settings = {"search_path": schema, "application_name": schema}  # finds the writer's backend
            return await asyncpg.create_pool(dsn, server_settings=settings, **kwargs)

        # A one-connection pool also proves upload intent does not nest another checkout.
        index = AsyncpgRenderIndex(dsn or DSN, IndexSettings(pool_max_size=1), pool_factory=factory)
        yield conn, index
    finally:
        if index is not None:
            await index.close()
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await conn.close()


async def gc_delete_pending(conn, store: FakeObjectStore, content_hash: str, key: str) -> str:
    """Cloud's `deletePending` for one queue row: 'absent', 'live' (row dropped, object kept) or 'deleted'."""
    async with conn.transaction():
        await conn.execute(LOCK_CONTENT, content_hash)
        if await conn.fetchval(CLOUD_PENDING_OBJECT, content_hash, key) is None:
            return "absent"
        live = {(row["hash"], row["cdn_path"]) for row in await conn.fetch(CLOUD_LIVE_ENTRY_PATHS, [content_hash])}
        if (content_hash, key) not in live:
            store.objects.pop(key, None)
        await conn.execute(CLOUD_FINISH_OBJECT_DELETE, content_hash, key)
        return "live" if (content_hash, key) in live else "deleted"


async def gc_run_due(conn, store: FakeObjectStore) -> list[str]:
    rows = await conn.fetch("SELECT content_hash, cdn_path FROM image_cache_object_deletions")
    return [await gc_delete_pending(conn, store, row["content_hash"], row["cdn_path"]) for row in rows]


async def expire_intents(conn) -> None:
    await conn.execute("UPDATE image_cache_object_deletions SET next_attempt_at = now() - interval '1 second'")


async def queue(conn) -> set[tuple[str, str]]:
    rows = await conn.fetch("SELECT content_hash, cdn_path FROM image_cache_object_deletions")
    return {(row["content_hash"], row["cdn_path"]) for row in rows}


async def wait_for_lock_waiter(conn) -> None:
    """Until the writer's backend is blocked on an advisory lock."""
    for _ in range(500):
        await conn.execute("SELECT pg_stat_clear_snapshot()")  # activity is cached per transaction otherwise
        waiting = await conn.fetchval(
            "SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a USING (pid) "
            "WHERE l.locktype = 'advisory' AND NOT l.granted AND a.application_name = current_schema()"
        )
        if waiting:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("writer never waited for the content lock")


def test_real_pg_put_runs_without_lock_or_transaction_and_dedups():
    async def run():
        async with database() as (conn, index):
            entered, release = asyncio.Event(), asyncio.Event()

            class Store(FakeObjectStore):
                async def write(self, key, data, *, content_type):
                    entered.set()
                    await release.wait()
                    await super().write(key, data, content_type=content_type)

            service, store, _ = _service(index=index, store=Store())
            task = asyncio.create_task(service.process(_payload(), _directive()))
            await asyncio.wait_for(entered.wait(), 5)
            try:
                # The intent is committed and visible to another connection before the object exists.
                assert await conn.fetchval("SELECT count(*) FROM image_cache_object_deletions") == 1
                # The PUT holds neither the content lock nor an open transaction.
                async with conn.transaction():
                    assert await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtextextended($1, 0))", DIGEST)
                states = await conn.fetch(
                    "SELECT state FROM pg_stat_activity WHERE application_name = current_schema()"
                )
                assert [row["state"] for row in states] == ["idle"]
                release.set()
                first = await asyncio.wait_for(task, 5)
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            assert first.ref is not None
            assert await conn.fetchval("SELECT count(*) FROM image_cache_object_deletions") == 0
            assert await conn.fetchval("SELECT count(*) FROM render_cache_index") == 1
            outcomes = await asyncio.gather(*(service.process(_payload(), _directive()) for _ in range(4)))
            assert all(outcome.ref.reused for outcome in outcomes)
            assert {outcome.ref.object_key for outcome in outcomes} == {first.ref.object_key}
            assert len(store.writes) == 1  # hits skip the PUT
            assert await conn.fetchval("SELECT writer_node FROM image_cache_entries") == "cn09"
            assert await conn.fetchval("SELECT count(*) FROM image_cache_object_deletions") == 0

    asyncio.run(run())


def test_real_pg_cancelled_put_keeps_committed_intent_and_releases_lock():
    async def run():
        async with database() as (conn, index):
            entered = asyncio.Event()

            class Store(FakeObjectStore):
                async def write(self, key, data, *, content_type):
                    entered.set()
                    await asyncio.Event().wait()

            service, _, _ = _service(index=index, store=Store())
            task = asyncio.create_task(service.process(_payload(), _directive()))
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await conn.fetchval("SELECT count(*) FROM image_cache_entries") == 0
            assert await conn.fetchval("SELECT count(*) FROM image_cache_object_deletions") == 1
            async with conn.transaction():
                await asyncio.wait_for(conn.execute(LOCK_CONTENT, DIGEST), 2)

    asyncio.run(run())


def test_real_pg_cancel_during_the_lock_wait_returns_a_clean_connection():
    async def run():
        async with database() as (conn, index):
            service, store, _ = _service(index=index)
            async with conn.transaction():
                await conn.execute(LOCK_CONTENT, DIGEST)
                task = asyncio.create_task(service.process(_payload(), _directive()))
                await wait_for_lock_waiter(conn)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert await conn.fetchval("SELECT count(*) FROM image_cache_entries") == 0
            assert len(await queue(conn)) == 1  # the uploaded candidate stays queued
            # The only pooled connection is reusable: not left inside the aborted writer transaction.
            outcome = await asyncio.wait_for(service.process(_payload(), _directive()), 5)
            assert outcome.ref is not None
            assert len(store.writes) == 2

    asyncio.run(run())


def test_real_pg_request_write_failure_rolls_back_content_and_preserves_intent():
    async def run():
        async with database() as (conn, index):
            await conn.execute("ALTER TABLE render_cache_index ADD CONSTRAINT reject_test CHECK (ttl_seconds < 0)")
            service, store, _ = _service(index=index)
            outcome = await service.process(_payload(), _directive())
            assert outcome.ref is None
            assert len(store.writes) == 1
            assert await conn.fetchval("SELECT count(*) FROM image_cache_entries") == 0
            assert await conn.fetchval("SELECT count(*) FROM render_cache_index") == 0
            assert await conn.fetchval("SELECT count(*) FROM image_cache_object_deletions") == 1
            # The pooled connection came back usable: no transaction left open, no lock held. (A fresh service:
            # this one now backs off after its failed write.)
            await conn.execute("ALTER TABLE render_cache_index DROP CONSTRAINT reject_test")
            retry, _, _ = _service(index=index, store=store)
            assert (await retry.process(_payload(), _directive())).ref is not None

    asyncio.run(run())


def test_real_pg_intent_expired_while_waiting_for_the_lock_is_never_recorded():
    async def run():
        async with database() as (conn, index):
            service, store, _ = _service(index=index)
            async with conn.transaction():
                await conn.execute(LOCK_CONTENT, DIGEST)
                task = asyncio.create_task(service.process(_payload(), _directive()))
                await wait_for_lock_waiter(conn)  # PREPARE and the PUT did not need the lock
                assert len(store.writes) == 1
                await expire_intents(conn)
            outcome = await asyncio.wait_for(task, 5)
            assert outcome.ref is None
            assert await conn.fetchval("SELECT count(*) FROM image_cache_entries") == 0
            # The uploaded object stays queued, and Cloud's GC then removes it.
            (key,) = store.objects
            assert await queue(conn) == {(DIGEST, key)}
            assert await gc_run_due(conn, store) == ["deleted"]
            assert store.objects == {}

    asyncio.run(run())


def test_real_pg_gc_consuming_the_intent_during_the_put_cannot_leak_or_record():
    # The PUT outlives the grace period: GC deletes the (not yet written) key and drops its queue row, then the
    # PUT lands. The locked write must neither record that key nor leave the object unqueued.
    async def run():
        async with database() as (conn, index):
            entered, release = asyncio.Event(), asyncio.Event()

            class Store(FakeObjectStore):
                async def write(self, key, data, *, content_type):
                    entered.set()
                    await release.wait()
                    await super().write(key, data, content_type=content_type)

            service, store, _ = _service(index=index, store=Store())
            task = asyncio.create_task(service.process(_payload(), _directive()))
            await asyncio.wait_for(entered.wait(), 5)
            await expire_intents(conn)
            assert await gc_run_due(conn, store) == ["deleted"]
            assert await queue(conn) == set()
            release.set()
            outcome = await asyncio.wait_for(task, 5)
            assert outcome.ref is None
            assert await conn.fetchval("SELECT count(*) FROM image_cache_entries") == 0
            assert await conn.fetchval("SELECT count(*) FROM render_cache_index") == 0
            (key,) = store.objects
            assert await queue(conn) == {(DIGEST, key)}  # put back, due now
            assert await gc_run_due(conn, store) == ["deleted"]
            assert store.objects == {}

    asyncio.run(run())


def test_real_pg_row_retired_while_the_writer_waits_for_the_lock_is_not_reused():
    # A hit: PREPARE sees a Garage row and skips the PUT. GC retires that row while holding the lock the writer
    # is waiting for. The locked write's snapshot postdates the lock, so it sees the row gone and records nothing.
    async def run():
        async with database() as (conn, index):
            stale = f"pjsk/api/pjsk/honor/{DIGEST}-old.png"
            await conn.execute(
                "INSERT INTO image_cache_entries (hash, group_name, cdn_path, size_bytes, storage_backend, "
                "media_type, last_referenced_at) VALUES ($1, 'pjsk', $2, 1, 'garage', 'image/png', "
                "now() - interval '30 days')",
                DIGEST,
                stale,
            )
            service, store, stats = _service(index=index)
            store.objects[stale] = b"old"
            async with conn.transaction():
                await conn.execute(LOCK_CONTENT, DIGEST)
                task = asyncio.create_task(service.process(_payload(), _directive()))
                await wait_for_lock_waiter(conn)
                cutoff = await conn.fetchval("SELECT now() - interval '7 days'")
                assert await conn.fetchval(CLOUD_RETIRE_OBJECT, DIGEST, cutoff, stale) == 1
            outcome = await asyncio.wait_for(task, 5)
            assert outcome.ref is None
            assert store.writes == []
            # Declined, not failed: a snapshot from before the lock wait (REPEATABLE READ) would hit a
            # serialization error on the retired row instead.
            assert stats.snapshot()["index_write_failures"] == 0
            assert await conn.fetchval("SELECT count(*) FROM image_cache_entries") == 0
            assert await conn.fetchval("SELECT count(*) FROM render_cache_index") == 0
            assert await gc_run_due(conn, store) == ["deleted"]
            assert store.objects == {}

    asyncio.run(run())


def test_real_pg_writer_losing_the_race_reuses_the_winner_and_leaves_its_upload_to_gc():
    async def run():
        async with database() as (conn, index):
            entered, release = asyncio.Event(), asyncio.Event()

            class Slow(FakeObjectStore):
                async def write(self, key, data, *, content_type):
                    entered.set()
                    await release.wait()
                    await super().write(key, data, content_type=content_type)

            store = Slow(bucket="image-cache")
            loser, _, loser_stats = _service(index=index, store=store)
            winner, _, winner_stats = _service(index=index, store=store)
            slow = asyncio.create_task(loser.process(_payload(), _directive()))
            await asyncio.wait_for(entered.wait(), 5)
            entered.clear()
            fast = asyncio.create_task(winner.process(_payload(), _directive(cache_key="other-key-0123456789")))
            await asyncio.wait_for(entered.wait(), 5)
            release.set()
            won = (await asyncio.wait_for(fast, 5)).ref
            lost = (await asyncio.wait_for(slow, 5)).ref
            assert won is not None
            assert lost is not None
            recorded = await conn.fetchval("SELECT cdn_path FROM image_cache_entries")
            assert {won.object_key, lost.object_key} == {recorded}
            assert len(store.objects) == 2
            (orphan,) = set(store.objects) - {recorded}
            assert await queue(conn) == {(DIGEST, orphan)}
            unrecorded = [stats.snapshot()["unrecorded_uploads"] for stats in (loser_stats, winner_stats)]
            assert sorted(unrecorded) == [0, 1]
            await expire_intents(conn)
            assert await gc_run_due(conn, store) == ["deleted"]
            assert set(store.objects) == {recorded}
            assert await conn.fetchval("SELECT count(*) FROM render_cache_index") == 2

    asyncio.run(run())


def test_real_pg_concurrent_writers_and_gc_never_record_a_deleted_object():
    # Randomised: writers for three payloads race a GC that makes every intent due at once, as if every PUT
    # outlived the grace period. Whatever interleaving happens, every recorded path and every returned ref must
    # name an object that exists, and every object must be either recorded or queued.
    async def run():
        async with database() as (conn, index):
            rng = random.Random(1234)

            class Jittery(FakeObjectStore):
                async def write(self, key, data, *, content_type):
                    await asyncio.sleep(rng.random() * 0.02)
                    await super().write(key, data, content_type=content_type)

            store = Jittery(bucket="image-cache")
            payloads = [_payload(b"\x89PNG\r\n\x1a\n" + bytes([n]) * 8) for n in range(3)]
            services = [_service(index=index, store=store)[0] for _ in range(4)]
            done = asyncio.Event()

            async def gc_loop():
                while not done.is_set():
                    await expire_intents(conn)
                    await gc_run_due(conn, store)
                    await asyncio.sleep(rng.random() * 0.01)

            async def writer(n: int):
                service = services[n % len(services)]
                payload = payloads[n % len(payloads)]
                return await service.process(payload, _directive(cache_key=f"key-{n:04d}-0123456789"))

            gc = asyncio.create_task(gc_loop())
            try:
                outcomes = await asyncio.gather(*(writer(n) for n in range(60)))
            finally:
                done.set()
                await gc
            refs = [outcome.ref for outcome in outcomes if outcome.ref is not None]
            assert refs, "no writer ever published"
            assert all(ref.object_key in store.objects for ref in refs)
            recorded = {row["cdn_path"] for row in await conn.fetch("SELECT cdn_path FROM image_cache_entries")}
            assert recorded <= set(store.objects)
            queued = {key for _, key in await queue(conn)}
            assert set(store.objects) <= recorded | queued
            await expire_intents(conn)
            await gc_run_due(conn, store)
            assert set(store.objects) == recorded

    asyncio.run(run())


# ------------------------------------------------------------------------------------------ round trips


class ReadyForQueryCounter:
    """A loopback TCP relay that counts the server's ReadyForQuery ('Z') messages: one per round trip.

    Every simple query and every extended-protocol Sync ends in exactly one 'Z', so the count is the number of
    times a client waited for the server, whatever the statements were.
    """

    def __init__(self, target_host: str, target_port: int) -> None:
        self.target = (target_host, target_port)
        self.count = 0
        self.server: asyncio.Server | None = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        up_reader, up_writer = await asyncio.open_connection(*self.target)

        async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter, parse: bool) -> None:
            buffer = b""
            try:
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
                    if not parse:
                        continue
                    buffer += data
                    while len(buffer) >= 5:
                        kind, size = buffer[:1], struct.unpack("!I", buffer[1:5])[0]
                        if len(buffer) < 1 + size:
                            break
                        if kind == b"Z":
                            self.count += 1
                        buffer = buffer[1 + size :]
            finally:
                dst.close()

        await asyncio.gather(pipe(reader, up_writer, False), pipe(up_reader, writer, True), return_exceptions=True)


def test_real_pg_artifact_step_is_four_round_trips_on_a_warm_connection():
    async def run():
        parts = urlsplit(DSN)
        counter = ReadyForQueryCounter(parts.hostname, parts.port or 5432)
        port = await counter.start()
        userinfo, at, _ = parts.netloc.rpartition("@")
        relayed = urlunsplit(parts._replace(netloc=f"{userinfo}{at}127.0.0.1:{port}"))
        try:
            async with database(relayed) as (_, index):
                service, _, _ = _service(index=index)
                # Warm up the pooled connection: pool creation, preflight and asyncpg's per-statement prepare.
                assert (await service.process(_payload(b"warm-up-miss"), _directive())).ref is not None
                assert (await service.process(_payload(b"warm-up-miss"), _directive())).ref.reused
                counter.count = 0
                miss = await service.process(_payload(b"measured-miss"), _directive())
                assert miss.ref is not None
                assert not miss.ref.reused
                assert counter.count == 4
                counter.count = 0
                hit = await service.process(_payload(b"measured-miss"), _directive(cache_key="hit-key-0123456789"))
                assert hit.ref is not None
                assert hit.ref.reused
                assert counter.count == 4
        finally:
            await counter.close()

    asyncio.run(run())
