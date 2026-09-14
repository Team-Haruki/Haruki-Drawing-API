"""`OpendalObjectStore`: error translation, layer order, read cap, lazy import."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
import subprocess
import sys
import types

import pytest

from src.settings import StorageProviderSettings
from src.storage import protocols
from src.storage.opendal_store import OpendalObjectStore, _to_ns
from src.storage.protocols import (
    ObjectStat,
    ObjectStore,
    StorageNotFound,
    StorageTooLarge,
    StorageUnavailable,
    StorageWriteFailed,
)

# ---- fake opendal module -------------------------------------------------------------------------------

_exc_module = types.ModuleType("opendal.exceptions")


def _opendal_exc(name: str) -> type[Exception]:
    cls = type(name, (Exception,), {})
    cls.__module__ = "opendal.exceptions"
    setattr(_exc_module, name, cls)
    return cls


NotFound = _opendal_exc("NotFound")
Unexpected = _opendal_exc("Unexpected")
Timeout = _opendal_exc("Timeout")
RateLimited = _opendal_exc("RateLimited")
Unsupported = _opendal_exc("Unsupported")


class _Meta:
    def __init__(self, size: int, last_modified=None, etag=None, content_type=None) -> None:
        self.content_length = size
        self.last_modified = last_modified
        self.etag = etag
        self.content_type = content_type


class FakeOperator:
    def __init__(self, scheme: str = "memory", **kwargs: str) -> None:
        self.scheme = scheme
        self.kwargs = kwargs
        self.layers: list[object] = []
        self.objects: dict[str, bytes] = {}
        self.stat_error: BaseException | None = None
        self.read_error: BaseException | None = None
        self.write_error: BaseException | None = None
        self.read_calls = 0
        self.stat_size_override: int | None = None
        self.writes: list[tuple[str, object, str]] = []

    def layer(self, layer: object) -> FakeOperator:
        self.layers.append(layer)
        return self

    async def stat(self, key: str) -> _Meta:
        if self.stat_error is not None:
            raise self.stat_error
        if key not in self.objects:
            raise NotFound(key)
        size = self.stat_size_override if self.stat_size_override is not None else len(self.objects[key])
        return _Meta(size, datetime(2026, 9, 13, tzinfo=UTC), '"etag"', "image/png")

    async def read(self, key: str) -> bytes:
        self.read_calls += 1
        if self.read_error is not None:
            raise self.read_error
        return self.objects[key]

    async def write(self, key: str, data, *, content_type: str) -> None:
        if self.write_error is not None:
            raise self.write_error
        self.writes.append((key, data, content_type))
        self.objects[key] = bytes(data)


def _layer(name: str):
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    return type(name, (), {"__init__": __init__})


@pytest.fixture
def fake_opendal(monkeypatch):
    mod = types.ModuleType("opendal")
    layers = types.ModuleType("opendal.layers")
    for name in ("RetryLayer", "TimeoutLayer", "ConcurrentLimitLayer"):
        setattr(layers, name, _layer(name))
    created: list[FakeOperator] = []

    def async_operator(scheme, **kwargs):
        op = FakeOperator(scheme, **kwargs)
        created.append(op)
        return op

    mod.AsyncOperator = async_operator
    mod.layers = layers
    mod.exceptions = _exc_module
    monkeypatch.setitem(sys.modules, "opendal", mod)
    monkeypatch.setitem(sys.modules, "opendal.layers", layers)
    monkeypatch.setitem(sys.modules, "opendal.exceptions", _exc_module)
    return created


def _store(op: FakeOperator | None = None, **kwargs) -> tuple[OpendalObjectStore, FakeOperator]:
    op = op or FakeOperator()
    return OpendalObjectStore(op, name="t", bucket="b", **kwargs), op


# ---- construction --------------------------------------------------------------------------------------


def test_from_provider_layer_order_retry_timeout_concurrent(fake_opendal):
    provider = StorageProviderSettings(scheme="s3", bucket="{region}-assets", endpoint="garage:3900", tls=False)
    store = OpendalObjectStore.from_provider(
        provider, region="jp", timeout=5.0, io_timeout=4.0, retries=2, concurrency=8, name="mirror", max_read_bytes=9
    )
    (op,) = fake_opendal
    assert [type(layer).__name__ for layer in op.layers] == ["RetryLayer", "TimeoutLayer", "ConcurrentLimitLayer"]
    retry, timeout, limit = op.layers
    assert retry.kwargs == {"max_times": 2, "jitter": True}
    assert timeout.kwargs == {"timeout": 5.0, "io_timeout": 4.0}
    assert limit.args == (8,)
    assert op.scheme == "s3"
    assert op.kwargs == {"bucket": "jp-assets", "endpoint": "http://garage:3900", "region": "garage"}
    assert (store.name, store.bucket, store.max_read_bytes) == ("mirror", "jp-assets", 9)


def test_objectstore_has_no_delete_or_list():
    for member in ("delete", "list", "exists", "remove_all", "scan"):
        assert not hasattr(ObjectStore, member)
    assert {"read", "stat", "write", "close"} <= set(dir(ObjectStore))
    assert isinstance(_store()[0], ObjectStore)


# ---- happy path ----------------------------------------------------------------------------------------


def test_stat_read_write_roundtrip():
    store, op = _store()

    async def run():
        assert await store.stat("a/b.png") is None
        await store.write("a/b.png", memoryview(b"hello"), content_type="image/png")
        stat = await store.stat("a/b.png")
        data, read_stat = await store.read("a/b.png")
        return stat, data, read_stat

    stat, data, read_stat = asyncio.run(run())
    assert op.writes == [("a/b.png", b"hello", "image/png")]
    expected_ns = int(datetime(2026, 9, 13, tzinfo=UTC).timestamp()) * 1_000_000_000
    assert stat == ObjectStat(5, expected_ns, '"etag"', "image/png")
    assert data == b"hello"
    assert read_stat == stat


def test_read_reconciles_size_with_bytes():
    store, op = _store()
    op.objects["k"] = b"abc"
    op.stat_size_override = 2
    data, stat = asyncio.run(store.read("k"))
    assert data == b"abc"
    assert stat.size == 3


def test_read_missing_raises_not_found():
    store, _ = _store()
    with pytest.raises(StorageNotFound):
        asyncio.run(store.read("missing"))


def test_invalid_key_rejected_before_io():
    store, op = _store()
    for key in ("", "/abs", "a/../b", "a//b", "./a", "a\\b"):
        with pytest.raises(ValueError, match="object key"):
            asyncio.run(store.stat(key))
    assert op.read_calls == 0


def test_close_is_idempotent():
    store, _ = _store()
    asyncio.run(store.close())
    asyncio.run(store.close())
    assert store.closed


# ---- read cap ------------------------------------------------------------------------------------------


def test_max_read_bytes_refused_via_stat_first():
    store, op = _store(max_read_bytes=3)
    op.objects["big"] = b"12345"
    with pytest.raises(StorageTooLarge):
        asyncio.run(store.read("big"))
    assert op.read_calls == 0


def test_max_bytes_argument_overrides_store_cap():
    store, op = _store(max_read_bytes=100)
    op.objects["big"] = b"12345"
    with pytest.raises(StorageTooLarge):
        asyncio.run(store.read("big", max_bytes=4))
    data, _ = asyncio.run(store.read("big", max_bytes=5))
    assert data == b"12345"


def test_max_read_bytes_rechecked_after_read():
    store, op = _store(max_read_bytes=3)
    op.objects["liar"] = b"12345"
    op.stat_size_override = 1
    with pytest.raises(StorageTooLarge):
        asyncio.run(store.read("liar"))
    assert op.read_calls == 1


# ---- error translation ---------------------------------------------------------------------------------


@pytest.mark.parametrize("exc", [Unexpected("boom"), Timeout("slow"), RateLimited("429"), Unsupported("x")])
def test_stat_and_read_errors_become_unavailable(exc):
    store, op = _store()
    op.objects["k"] = b"v"
    op.stat_error = exc
    with pytest.raises(StorageUnavailable) as info:
        asyncio.run(store.stat("k"))
    assert not isinstance(info.value, StorageWriteFailed)
    op.stat_error = None
    op.read_error = exc
    with pytest.raises(StorageUnavailable) as info:
        asyncio.run(store.read("k"))
    assert not isinstance(info.value, StorageWriteFailed)
    assert info.value.__cause__ is exc


@pytest.mark.parametrize("exc", [Unexpected("boom"), Timeout("slow"), TimeoutError(), OSError("reset")])
def test_write_errors_become_write_failed(exc):
    store, op = _store()
    op.write_error = exc
    with pytest.raises(StorageWriteFailed):
        asyncio.run(store.write("k", b"v", content_type="image/png"))


def test_asyncio_timeout_on_read_is_unavailable():
    store, op = _store()
    op.objects["k"] = b"v"
    op.read_error = TimeoutError()
    with pytest.raises(StorageUnavailable):
        asyncio.run(store.read("k"))


def test_not_found_on_read_body_translates():
    store, op = _store()
    op.objects["k"] = b"v"
    op.read_error = NotFound("gone")
    with pytest.raises(StorageNotFound):
        asyncio.run(store.read("k"))


def test_non_storage_errors_propagate():
    store, op = _store()
    op.objects["k"] = b"v"
    op.stat_error = KeyError("bug")
    with pytest.raises(KeyError):
        asyncio.run(store.stat("k"))
    op.stat_error = None
    op.read_error = KeyError("bug")
    with pytest.raises(KeyError):
        asyncio.run(store.read("k"))
    op.write_error = KeyError("bug")
    with pytest.raises(KeyError):
        asyncio.run(store.write("k", b"v", content_type="image/png"))


def test_to_ns():
    assert _to_ns(None) is None
    assert _to_ns(123) == 123
    assert _to_ns("2026") is None
    assert _to_ns(datetime(1970, 1, 1, 0, 0, 1)) == 1_000_000_000
    aware = datetime(1970, 1, 1, 9, 0, 0, 500, tzinfo=timezone(timedelta(hours=9)))
    assert _to_ns(aware) == 500_000


# ---- lazy import ---------------------------------------------------------------------------------------


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False)


def test_importing_storage_does_not_import_opendal():
    result = _run_python(
        "import sys\n"
        "import src.storage, src.storage.protocols, src.storage.provider, src.storage.opendal_store\n"
        "assert 'opendal' not in sys.modules, sorted(m for m in sys.modules if m.startswith('opendal'))\n"
    )
    assert result.returncode == 0, result.stderr


def test_storage_imports_with_opendal_blocked():
    result = _run_python(
        "import sys\n"
        "sys.modules['opendal'] = None\n"
        "import src.storage.protocols, src.storage.provider, src.storage.opendal_store\n"
        "from src.settings import StorageProviderSettings\n"
        "from src.storage.opendal_store import OpendalObjectStore\n"
        "try:\n"
        "    OpendalObjectStore.from_provider(StorageProviderSettings(scheme='memory'), region=None, timeout=1,\n"
        "        io_timeout=1, retries=0, concurrency=1, name='x')\n"
        "except ImportError:\n"
        "    print('import-error')\n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "import-error"


def test_protocols_module_exports():
    assert protocols.validate_object_key("a/b.png") == "a/b.png"
    with pytest.raises(ValueError, match="object key"):
        protocols.validate_object_key(None)  # type: ignore[arg-type]
