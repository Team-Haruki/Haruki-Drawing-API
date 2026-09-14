"""`FakeObjectStore` and `OpendalObjectStore(AsyncOperator("memory"))` pass the same protocol test."""

from __future__ import annotations

import asyncio

import pytest

from src.storage.protocols import ObjectStore, StorageNotFound, StorageTooLarge, StorageUnavailable
from tests.storage_fakes import FakeObjectStore


@pytest.fixture(params=["fake", "opendal-memory"])
def any_store(request):
    if request.param == "fake":
        return request.getfixturevalue("memory_store")
    return request.getfixturevalue("opendal_memory_store")


def test_object_store_protocol(any_store):
    store = any_store
    assert isinstance(store, ObjectStore)
    assert isinstance(store.name, str)
    assert isinstance(store.bucket, str)

    async def run():
        assert await store.stat("pjsk/api/x/abc.png") is None
        with pytest.raises(StorageNotFound):
            await store.read("pjsk/api/x/abc.png")

        await store.write("pjsk/api/x/abc.png", b"\x89PNG-bytes", content_type="image/png")
        await store.write("pjsk/api/x/def.webp", memoryview(b"webp"), content_type="image/webp")

        stat = await store.stat("pjsk/api/x/abc.png")
        assert stat is not None
        assert stat.size == len(b"\x89PNG-bytes")
        assert stat.last_modified_ns is None or isinstance(stat.last_modified_ns, int)

        data, read_stat = await store.read("pjsk/api/x/abc.png")
        assert data == b"\x89PNG-bytes"
        assert isinstance(data, bytes)
        assert read_stat.size == len(data)

        data, _ = await store.read("pjsk/api/x/def.webp", max_bytes=4)
        assert data == b"webp"
        with pytest.raises(StorageTooLarge):
            await store.read("pjsk/api/x/abc.png", max_bytes=3)

        await store.write("pjsk/api/x/abc.png", b"new", content_type="image/png")
        assert (await store.read("pjsk/api/x/abc.png"))[0] == b"new"

        for bad in ("/abs", "a/../b", ""):
            with pytest.raises(ValueError, match="object key"):
                await store.stat(bad)

        await store.close()

    asyncio.run(run())


def test_fake_records_operations():
    store = FakeObjectStore({"a": b"1"}, last_modified={"a": 42})

    async def run():
        assert (await store.stat("a")).last_modified_ns == 42
        await store.read("a")
        await store.write("b", b"22", content_type="text/plain")

    asyncio.run(run())
    assert store.stats == ["a"]
    assert store.reads == ["a"]
    assert store.writes == [("b", 2, "text/plain")]
    assert store.ops == 3


def test_fake_fail_everything():
    store = FakeObjectStore({"a": b"1"}, fail=StorageUnavailable("down"))
    with pytest.raises(StorageUnavailable, match="down"):
        asyncio.run(store.read("a"))
    assert store.reads == []


def test_fake_fail_keys_default_error():
    store = FakeObjectStore({"a": b"1", "b": b"2"}, fail_keys={"b"})

    async def run():
        await store.read("a")
        with pytest.raises(StorageUnavailable):
            await store.read("b")

    asyncio.run(run())


def test_fake_fail_after():
    store = FakeObjectStore({"a": b"1"}, fail_after=2, fail=StorageNotFound("later"))

    async def run():
        await store.stat("a")
        await store.read("a")
        with pytest.raises(StorageNotFound, match="later"):
            await store.read("a")

    asyncio.run(run())


def test_fake_max_read_bytes_and_delay():
    store = FakeObjectStore({"a": b"12345"}, max_read_bytes=2, delay=0.001)
    with pytest.raises(StorageTooLarge):
        asyncio.run(store.read("a"))
