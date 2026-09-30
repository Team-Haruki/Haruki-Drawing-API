"""PostgreSQL render index: coordinated content writes — Haruki-Cloud owns and runs the schema migrations.

Importing this package never imports `asyncpg`; `src.index.asyncpg_index` imports it lazily, on first use,
inside the running event loop.
"""

from src.index.protocols import (
    ContentRow,
    IndexSchemaError,
    IndexUnavailable,
    IndexWriteFailed,
    RecordResult,
    RenderIndex,
    RequestRow,
)

__all__ = [
    "ContentRow",
    "IndexSchemaError",
    "IndexUnavailable",
    "IndexWriteFailed",
    "RecordResult",
    "RenderIndex",
    "RequestRow",
]
