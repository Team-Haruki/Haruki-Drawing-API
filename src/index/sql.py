"""The SQL statements Drawing runs against the render index (plan §9.2, addendum A5), pinned as text.

Index reads/writes, shared locks and upload intents only. Every column named here is shipped by
Haruki-Cloud's canonical schema. Drawing never runs schema changes, and never touches
`render_cache_index.last_used_at` after the insert (Cloud bumps it on lookup).

`UPSERT_CONTENT` guards only the backend-shaped columns (`cdn_path`, `size_bytes`, `media_type`) per column:
an existing `legacy_disk` row is upgraded, an existing `garage` row keeps its path forever (the reuse rule of
addendum A2), and `last_referenced_at` plus the NULL-is-infinite `expires_at` merge run on every upsert.
A statement-level guard would freeze the retention clock of `garage` rows — do not "simplify" it.

A write is four round trips: `PREPARE_UPLOAD`, then the object PUT with no transaction open, then
`begin_and_lock()`, `RECORD_UPLOAD` and `COMMIT` on one connection. docs/artifact-storage.md §6 has the
argument for why Cloud's GC cannot delete an object this protocol records.
"""

import re

# Cloud's `ContentLockSQL`, character for character: both sides must hash the same key.
LOCK_CONTENT = "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))"

_HEX_HASH = re.compile(r"[0-9a-f]{64}")


def begin_and_lock(content_hash: str) -> str:
    """`BEGIN` plus `LOCK_CONTENT` as one simple-protocol query: one round trip instead of two.

    The simple protocol takes no parameters, so the hash is inlined as a literal. It is always our own
    lowercase SHA-256 hex digest; anything else is refused rather than quoted. READ COMMITTED is pinned so that
    the next statement takes its snapshot after the lock is granted, whatever the server default is: under
    REPEATABLE READ the snapshot would date from before the lock wait and miss a GC commit made meanwhile.
    """
    if not _HEX_HASH.fullmatch(content_hash):
        raise ValueError("content hash must be 64 lowercase hex digits")
    return "BEGIN ISOLATION LEVEL READ COMMITTED; " + LOCK_CONTENT.replace("$1", f"'{content_hash}'")


# The lookup rides on the intent statement: a hash that already has a Garage row gets no intent and no PUT;
# anything else commits Cloud's `registerUploadSQL` intent before the PUT starts.
PREPARE_UPLOAD = (
    "WITH existing AS (SELECT cdn_path FROM image_cache_entries WHERE hash = $1 AND storage_backend = 'garage'), "
    "intent AS (INSERT INTO image_cache_object_deletions (content_hash, cdn_path, next_attempt_at) "
    "SELECT $1, $2, now() + interval '5 minutes' WHERE NOT EXISTS (SELECT 1 FROM existing) "
    "ON CONFLICT (content_hash, cdn_path) DO NOTHING) "
    "SELECT cdn_path FROM existing"
)

PREFLIGHT_REQUEST = (
    "SELECT request_key, content_hash, api_path, user_id, group_name, key_version, "
    "ttl_seconds, expires_at, created_at, last_used_at FROM render_cache_index LIMIT 0"
)

PREFLIGHT_CONTENT = (
    "SELECT hash, group_name, cdn_path, file_path, size_bytes, storage_backend, media_type, "
    "expires_at, last_referenced_at, writer_node, written_at FROM image_cache_entries LIMIT 0"
)

SELECT_CONTENT = (
    "SELECT hash, group_name, cdn_path, storage_backend, media_type, size_bytes, expires_at, writer_node, written_at "
    "FROM image_cache_entries WHERE hash = $1"
)

_CONTENT_INSERT = (
    "INSERT INTO image_cache_entries "
    "(hash, group_name, cdn_path, file_path, size_bytes, storage_backend, media_type, expires_at, "
    "last_referenced_at, created_at, writer_node, written_at) "
)
_CONTENT_VALUES = "$1, $2, $3, NULL, $4, 'garage', $5, $6, now(), now(), $7, $8"
_CONTENT_CONFLICT = (
    "ON CONFLICT (hash) DO UPDATE SET "
    "cdn_path = CASE WHEN image_cache_entries.storage_backend IS DISTINCT FROM 'garage' "
    "THEN EXCLUDED.cdn_path ELSE image_cache_entries.cdn_path END, "
    "size_bytes = CASE WHEN image_cache_entries.storage_backend IS DISTINCT FROM 'garage' "
    "THEN EXCLUDED.size_bytes ELSE image_cache_entries.size_bytes END, "
    "media_type = CASE WHEN image_cache_entries.storage_backend IS DISTINCT FROM 'garage' "
    "THEN EXCLUDED.media_type ELSE image_cache_entries.media_type END, "
    "writer_node = CASE WHEN image_cache_entries.storage_backend IS DISTINCT FROM 'garage' "
    "THEN EXCLUDED.writer_node ELSE image_cache_entries.writer_node END, "
    "written_at = CASE WHEN image_cache_entries.storage_backend IS DISTINCT FROM 'garage' "
    "THEN EXCLUDED.written_at ELSE image_cache_entries.written_at END, "
    "storage_backend = 'garage', "
    "file_path = NULL, "
    "last_referenced_at = now(), "
    "expires_at = CASE "
    "WHEN image_cache_entries.expires_at IS NULL OR EXCLUDED.expires_at IS NULL THEN NULL "
    "ELSE GREATEST(image_cache_entries.expires_at, EXCLUDED.expires_at) END"
)

UPSERT_CONTENT = _CONTENT_INSERT + "VALUES (" + _CONTENT_VALUES + ") " + _CONTENT_CONFLICT

_REQUEST_INSERT = (
    "INSERT INTO render_cache_index "
    "(request_key, content_hash, api_path, user_id, group_name, key_version, ttl_seconds, expires_at, "
    "created_at, last_used_at) "
)
_REQUEST_VALUES = "$1, $2, $3, $4, $5, $6, $7, $8, now(), now()"
_REQUEST_CONFLICT = (
    "ON CONFLICT (request_key) DO UPDATE SET "
    "content_hash = EXCLUDED.content_hash, api_path = EXCLUDED.api_path, user_id = EXCLUDED.user_id, "
    "group_name = EXCLUDED.group_name, key_version = EXCLUDED.key_version, "
    "ttl_seconds = EXCLUDED.ttl_seconds, expires_at = EXCLUDED.expires_at, last_used_at = now()"
)

UPSERT_REQUEST = _REQUEST_INSERT + "VALUES (" + _REQUEST_VALUES + ") " + _REQUEST_CONFLICT


def _shift(text: str, offset: int) -> str:
    return re.sub(r"\$(\d+)", lambda match: f"${int(match[1]) + offset}", text)


# One statement for both rows; the surrounding writer transaction owns the hash lock.
RECORD = (
    "WITH existing AS (SELECT storage_backend FROM image_cache_entries WHERE hash = $1), upserted AS ("
    + UPSERT_CONTENT
    + " RETURNING cdn_path, media_type, size_bytes), indexed AS ("
    + _shift(UPSERT_REQUEST, 8)
    + ") SELECT upserted.cdn_path, upserted.media_type, upserted.size_bytes, "
    "(SELECT storage_backend FROM existing) AS prior_backend FROM upserted"
)

# `RECORD` gated on ownership, plus Cloud's `verifyUploadSQL` and `finishObjectDeleteSQL`, in one statement.
# It runs under the hash lock taken by `begin_and_lock()`, as the next statement, so its snapshot postdates
# the lock. Both rows are written only when the candidate path ($3) still holds an unclaimed, unexpired intent,
# or when a Garage row already owns the hash (it keeps its path, addendum A2). The intent is removed only when
# the candidate path is the one recorded. When the candidate was uploaded ($17) but is not recorded, its intent
# is put back if GC already consumed it, so the object is always either recorded or queued for deletion.
# No row back means no index row was written: the caller commits (keeping that intent) and returns bytes.
RECORD_UPLOAD = (
    "WITH intent AS (SELECT 1 FROM image_cache_object_deletions WHERE content_hash = $1 AND cdn_path = $3 "
    "AND attempts = 0 AND next_attempt_at > clock_timestamp() FOR UPDATE), "
    "existing AS (SELECT storage_backend FROM image_cache_entries WHERE hash = $1), upserted AS ("
    + _CONTENT_INSERT
    + "SELECT "
    + _CONTENT_VALUES
    + " WHERE EXISTS (SELECT 1 FROM intent) OR EXISTS (SELECT 1 FROM existing WHERE storage_backend = 'garage') "
    + _CONTENT_CONFLICT
    + " RETURNING cdn_path, media_type, size_bytes, writer_node, written_at), indexed AS ("
    + _REQUEST_INSERT
    + "SELECT "
    + _shift(_REQUEST_VALUES, 8)
    + " FROM upserted "
    + _REQUEST_CONFLICT
    + "), finished AS (DELETE FROM image_cache_object_deletions WHERE content_hash = $1 AND cdn_path = $3 "
    "AND EXISTS (SELECT 1 FROM upserted WHERE upserted.cdn_path = $3)), "
    "requeued AS (INSERT INTO image_cache_object_deletions (content_hash, cdn_path) SELECT $1, $3 "
    "WHERE $17 AND NOT EXISTS (SELECT 1 FROM upserted WHERE upserted.cdn_path = $3) "
    "ON CONFLICT (content_hash, cdn_path) DO NOTHING) "
    "SELECT upserted.cdn_path, upserted.media_type, upserted.size_bytes, upserted.writer_node, "
    "upserted.written_at, (SELECT storage_backend FROM existing) AS prior_backend FROM upserted"
)

COMMIT = "COMMIT"
ROLLBACK = "ROLLBACK"

ALL_STATEMENTS: dict[str, str] = {
    "RECORD": RECORD,
    "RECORD_UPLOAD": RECORD_UPLOAD,
    "LOCK_CONTENT": LOCK_CONTENT,
    "PREPARE_UPLOAD": PREPARE_UPLOAD,
    "PREFLIGHT_REQUEST": PREFLIGHT_REQUEST,
    "PREFLIGHT_CONTENT": PREFLIGHT_CONTENT,
    "SELECT_CONTENT": SELECT_CONTENT,
    "UPSERT_CONTENT": UPSERT_CONTENT,
    "UPSERT_REQUEST": UPSERT_REQUEST,
}
