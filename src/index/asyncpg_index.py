"""`RenderIndex` over an asyncpg pool (plan §9.4).

`asyncpg` is imported lazily inside the pool factory, on first use, inside the running event loop — never at
import time and never at startup. A failed connect or a missing Cloud schema starts a
`connect_retry_seconds` backoff window during which no PostgreSQL round trip is attempted at all, so shipping
Drawing with a DSN before Cloud's schema lands costs one failing round trip per window, not one per request.

The DSN password never reaches a log record or a `repr`: every rendering goes through `dsn_redacted()`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from src.index.protocols import ContentRow, IndexSchemaError, IndexUnavailable, IndexWriteFailed, RequestRow
from src.index.sql import PREFLIGHT_CONTENT, PREFLIGHT_REQUEST, SELECT_CONTENT, UPSERT_CONTENT, UPSERT_REQUEST

if TYPE_CHECKING:  # pragma: no cover
    from src.settings import IndexSettings

logger = logging.getLogger("src.index.asyncpg_index")

PoolFactory = Callable[..., Awaitable[Any]]

# Matched on class names (the whole MRO) so tests can raise stand-ins without importing asyncpg.
_SCHEMA_ERROR_NAMES = frozenset({"UndefinedTableError", "UndefinedColumnError"})
_TRANSPORT_ERROR_NAMES = frozenset(
    {
        "PostgresConnectionError",
        "ConnectionDoesNotExistError",
        "ConnectionFailureError",
        "CannotConnectNowError",
        "TooManyConnectionsError",
        "InterfaceError",
        "InternalClientError",
    }
)


def _mro_names(exc: BaseException) -> set[str]:
    return {cls.__name__ for cls in type(exc).__mro__}


def _is_schema_error(exc: BaseException) -> bool:
    return bool(_mro_names(exc) & _SCHEMA_ERROR_NAMES)


def _is_transport_error(exc: BaseException) -> bool:
    return isinstance(exc, (OSError, TimeoutError)) or bool(_mro_names(exc) & _TRANSPORT_ERROR_NAMES)


def dsn_redacted(dsn: str) -> str:
    """Render a DSN as `user@host:port/db` with the password (and every query parameter) removed."""
    text = (dsn or "").strip()
    if not text:
        return "<empty>"
    if "://" in text:
        try:
            parts = urlsplit(text)
            user = unquote(parts.username) if parts.username else ""
            host = parts.hostname or ""
            port = parts.port
        except ValueError:
            return "<unparseable dsn>"
        location = f"{host}:{port}" if port else host
        database = parts.path.lstrip("/")
        prefix = f"{user}@" if user else ""
        return f"{prefix}{location}/{database}"
    # libpq keyword/value form: keep only the identifying keys.
    fields: dict[str, str] = {}
    for token in text.split():
        key, sep, value = token.partition("=")
        if sep:
            fields[key.strip().lower()] = value.strip().strip("'\"")
    if not fields:
        return "<unparseable dsn>"
    user = fields.get("user", "")
    host = fields.get("host", "")
    port = fields.get("port", "")
    location = f"{host}:{port}" if port else host
    prefix = f"{user}@" if user else ""
    return f"{prefix}{location}/{fields.get('dbname', '')}"


async def _asyncpg_pool_factory(dsn: str, **kwargs: Any) -> Any:
    import asyncpg  # lazy by design: importing src.index never imports the wheel

    return await asyncpg.create_pool(dsn, **kwargs)


class AsyncpgRenderIndex:
    """Lazy pool, preflight once per healthy period, backoff after any schema or transport failure."""

    def __init__(
        self,
        dsn: str,
        settings: IndexSettings,
        *,
        pool_factory: PoolFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._dsn = dsn
        self._redacted = dsn_redacted(dsn)
        self._settings = settings
        self._pool_factory: PoolFactory = pool_factory or _asyncpg_pool_factory
        self._clock = clock
        self._pool: Any = None
        self._lock: asyncio.Lock | None = None
        self._ready = False
        self._retry_at = 0.0
        self._backoff_error: type[Exception] = IndexUnavailable
        self._schema_logged = False
        self._closed = False
        self.stats: dict[str, int] = {
            "pool_created": 0,
            "preflight_ok": 0,
            "schema_missing": 0,
            "unavailable": 0,
            "backoff_skips": 0,
            "lookups": 0,
            "records": 0,
            "write_failures": 0,
        }

    def __repr__(self) -> str:
        return f"AsyncpgRenderIndex(dsn={self._redacted!r}, ready={self._ready})"

    __str__ = __repr__

    @property
    def dsn_redacted(self) -> str:
        return self._redacted

    @property
    def ready(self) -> bool:
        return self._ready

    def in_backoff(self) -> bool:
        return self._clock() < self._retry_at

    # ------------------------------------------------------------------ failure handling

    def _scrub(self, text: str) -> str:
        password = ""
        if "://" in self._dsn:
            try:
                password = urlsplit(self._dsn).password or ""
            except ValueError:
                password = ""
        else:
            for token in self._dsn.split():
                key, sep, value = token.partition("=")
                if sep and key.strip().lower() == "password":
                    password = value.strip().strip("'\"")
        text = text.replace(self._dsn, self._redacted) if self._dsn else text
        if password:
            text = text.replace(password, "***").replace(unquote(password), "***")
        return text

    def _start_backoff(self, error_type: type[Exception]) -> None:
        self._ready = False
        self._backoff_error = error_type
        self._retry_at = self._clock() + max(0.0, float(self._settings.connect_retry_seconds))

    def _schema_missing(self, exc: BaseException) -> IndexSchemaError:
        self.stats["schema_missing"] += 1
        self._start_backoff(IndexSchemaError)
        if not self._schema_logged:
            self._schema_logged = True
            logger.error(
                "render index: Cloud DDL not shipped (%s: %s) dsn=%s; index writes disabled for %.0fs windows",
                type(exc).__name__,
                self._scrub(str(exc)),
                self._redacted,
                self._settings.connect_retry_seconds,
            )
        return IndexSchemaError(f"render index schema missing: {type(exc).__name__}")

    def _unavailable(self, exc: BaseException, stage: str, error_type: type[IndexUnavailable]) -> IndexUnavailable:
        self.stats["unavailable"] += 1
        self._start_backoff(IndexUnavailable)
        logger.warning(
            "render index unavailable during %s (%s: %s) dsn=%s; retrying in %.0fs",
            stage,
            type(exc).__name__,
            self._scrub(str(exc)),
            self._redacted,
            self._settings.connect_retry_seconds,
        )
        return error_type(f"render index unavailable during {stage}: {type(exc).__name__}")

    def _classify(self, exc: Exception, stage: str, *, write: bool) -> Exception:
        if _is_schema_error(exc):
            return self._schema_missing(exc)
        error_type: type[IndexUnavailable] = IndexWriteFailed if write else IndexUnavailable
        if _is_transport_error(exc):
            return self._unavailable(exc, stage, error_type)
        if write:
            self.stats["write_failures"] += 1
        logger.warning(
            "render index %s failed (%s: %s) dsn=%s",
            stage,
            type(exc).__name__,
            self._scrub(str(exc)),
            self._redacted,
        )
        return error_type(f"render index {stage} failed: {type(exc).__name__}")

    # ------------------------------------------------------------------ pool + preflight

    def _check_backoff(self) -> None:
        if self._closed:
            raise IndexUnavailable("render index is closed")
        if self.in_backoff():
            self.stats["backoff_skips"] += 1
            raise self._backoff_error("render index in backoff window")

    async def _get_pool(self) -> Any:
        if self._pool is not None:
            return self._pool
        try:
            pool = await self._pool_factory(
                self._dsn,
                min_size=self._settings.pool_min_size,
                max_size=self._settings.pool_max_size,
                timeout=self._settings.connect_timeout_seconds,
                command_timeout=self._settings.command_timeout_seconds,
            )
        except Exception as exc:
            if _is_schema_error(exc):
                raise self._schema_missing(exc) from None
            raise self._unavailable(exc, "connect", IndexUnavailable) from None
        self._pool = pool
        self.stats["pool_created"] += 1
        return pool

    async def _run_preflight(self) -> None:
        pool = await self._get_pool()
        try:
            async with pool.acquire(timeout=self._settings.connect_timeout_seconds) as conn:
                await conn.execute(PREFLIGHT_REQUEST)
                await conn.execute(PREFLIGHT_CONTENT)
        except Exception as exc:
            if _is_schema_error(exc):
                raise self._schema_missing(exc) from None
            raise self._unavailable(exc, "preflight", IndexUnavailable) from None
        self._ready = True
        self.stats["preflight_ok"] += 1

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _ensure_ready(self) -> Any:
        self._check_backoff()
        if self._ready:
            return self._pool
        async with self._get_lock():
            self._check_backoff()
            if not self._ready:
                await self._run_preflight()
        return self._pool

    async def preflight(self) -> None:
        await self._ensure_ready()

    # ------------------------------------------------------------------ protocol

    async def lookup_content(self, content_hash: str) -> ContentRow | None:
        pool = await self._ensure_ready()
        self.stats["lookups"] += 1
        try:
            async with pool.acquire(timeout=self._settings.connect_timeout_seconds) as conn:
                row = await conn.fetchrow(SELECT_CONTENT, content_hash)
        except Exception as exc:
            raise self._classify(exc, "lookup", write=False) from None
        if row is None:
            return None
        return ContentRow(
            hash=row["hash"],
            group_name=row["group_name"],
            cdn_path=row["cdn_path"],
            storage_backend=row["storage_backend"],
            media_type=row["media_type"],
            size_bytes=row["size_bytes"],
            expires_at=row["expires_at"],
        )

    async def record(self, content: ContentRow, request: RequestRow) -> None:
        pool = await self._ensure_ready()
        self.stats["records"] += 1
        try:
            async with pool.acquire(timeout=self._settings.connect_timeout_seconds) as conn:
                async with conn.transaction():
                    await conn.execute(
                        UPSERT_CONTENT,
                        content.hash,
                        content.group_name,
                        content.cdn_path,
                        content.size_bytes,
                        content.media_type,
                        content.expires_at,
                    )
                    await conn.execute(
                        UPSERT_REQUEST,
                        request.request_key,
                        request.content_hash,
                        request.api_path,
                        request.user_id,
                        request.group_name,
                        request.key_version,
                        request.ttl_seconds,
                        request.expires_at,
                    )
        except Exception as exc:
            raise self._classify(exc, "record", write=True) from None

    async def close(self) -> None:
        self._closed = True
        self._ready = False
        pool, self._pool = self._pool, None
        if pool is None:
            return
        try:
            await pool.close()
        except Exception as exc:  # shutdown never raises
            logger.warning("render index close failed (%s) dsn=%s", type(exc).__name__, self._redacted)
