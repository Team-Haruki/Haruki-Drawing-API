"""Optional loopback-only PostgreSQL verification, using a new schema per test.

HARUKI_TEST_INDEX_DSN=postgresql://postgres@127.0.0.1:55439/postgres?sslmode=disable
The test creates and drops only its random drawing_test_* schema.
"""

import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from src.index.asyncpg_index import AsyncpgRenderIndex
from src.index.sql import LOCK_CONTENT
from src.settings import IndexSettings
from tests.storage_fakes import FakeObjectStore
from tests.test_artifact_service import DIGEST, _directive, _payload, _service

DSN = os.environ.get("HARUKI_TEST_INDEX_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="HARUKI_TEST_INDEX_DSN not configured")


@asynccontextmanager
async def database():
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
            return await asyncpg.create_pool(dsn, server_settings={"search_path": schema}, **kwargs)

        # A one-connection pool also proves upload intent does not nest another checkout.
        index = AsyncpgRenderIndex(DSN, IndexSettings(pool_max_size=1), pool_factory=factory)
        yield conn, index
    finally:
        if index is not None:
            await index.close()
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await conn.close()


def test_real_pg_writer_lock_durable_intent_and_dedup():
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
                # The intent is visible to another connection before object publication.
                assert await conn.fetchval("SELECT count(*) FROM image_cache_object_deletions") == 1
                async with conn.transaction():
                    assert not await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtextextended($1, 0))", DIGEST)
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
            assert len(store.writes) == 1
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

    asyncio.run(run())


def test_real_pg_expired_intent_after_lock_wait_never_starts_put():
    async def run():
        async with database() as (conn, index):
            prepared = asyncio.Event()
            original = index.prepare_upload

            async def prepare(content_hash, path):
                await original(content_hash, path)
                prepared.set()

            index.prepare_upload = prepare
            service, store, _ = _service(index=index)
            async with conn.transaction():
                await conn.execute(LOCK_CONTENT, DIGEST)
                task = asyncio.create_task(service.process(_payload(), _directive()))
                await asyncio.wait_for(prepared.wait(), 5)
                await conn.execute(
                    "UPDATE image_cache_object_deletions SET next_attempt_at = now() - interval '1 second'"
                )
            outcome = await asyncio.wait_for(task, 5)
            assert outcome.ref is None
            assert store.writes == []
            assert await conn.fetchval("SELECT count(*) FROM image_cache_object_deletions") == 1

    asyncio.run(run())
