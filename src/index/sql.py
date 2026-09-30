"""The SQL statements Drawing runs against the render index (plan §9.2, addendum A5), pinned as text.

Index reads/writes, shared locks and upload intents only. Every column named here is shipped by
Haruki-Cloud's canonical schema. Drawing never runs schema changes, and never touches
`render_cache_index.last_used_at` after the insert (Cloud bumps it on lookup).

`UPSERT_CONTENT` guards only the backend-shaped columns (`cdn_path`, `size_bytes`, `media_type`) per column:
an existing `legacy_disk` row is upgraded, an existing `garage` row keeps its path forever (the reuse rule of
addendum A2), and `last_referenced_at` plus the NULL-is-infinite `expires_at` merge run on every upsert.
A statement-level guard would freeze the retention clock of `garage` rows — do not "simplify" it.
"""

LOCK_CONTENT = "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))"

PREPARE_UPLOAD = (
    "INSERT INTO image_cache_object_deletions (content_hash, cdn_path, next_attempt_at) "
    "VALUES ($1, $2, now() + interval '5 minutes') "
    "ON CONFLICT (content_hash, cdn_path) DO NOTHING"
)

FINISH_UPLOAD = "DELETE FROM image_cache_object_deletions WHERE content_hash = $1 AND cdn_path = $2"

CHECK_UPLOAD = (
    "SELECT 1 FROM image_cache_object_deletions WHERE content_hash = $1 AND cdn_path = $2 "
    "AND attempts = 0 AND next_attempt_at > clock_timestamp() FOR UPDATE"
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

UPSERT_CONTENT = (
    "INSERT INTO image_cache_entries "
    "(hash, group_name, cdn_path, file_path, size_bytes, storage_backend, media_type, expires_at, "
    "last_referenced_at, created_at, writer_node, written_at) "
    "VALUES ($1, $2, $3, NULL, $4, 'garage', $5, $6, now(), now(), $7, $8) "
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

UPSERT_REQUEST = (
    "INSERT INTO render_cache_index "
    "(request_key, content_hash, api_path, user_id, group_name, key_version, ttl_seconds, expires_at, "
    "created_at, last_used_at) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now(), now()) "
    "ON CONFLICT (request_key) DO UPDATE SET "
    "content_hash = EXCLUDED.content_hash, api_path = EXCLUDED.api_path, user_id = EXCLUDED.user_id, "
    "group_name = EXCLUDED.group_name, key_version = EXCLUDED.key_version, "
    "ttl_seconds = EXCLUDED.ttl_seconds, expires_at = EXCLUDED.expires_at, last_used_at = now()"
)

# One statement for both rows; the surrounding writer transaction owns the hash lock.
RECORD = (
    "WITH existing AS (SELECT storage_backend FROM image_cache_entries WHERE hash = $1), upserted AS ("
    + UPSERT_CONTENT
    + " RETURNING cdn_path, media_type, size_bytes), indexed AS ("
    + UPSERT_REQUEST.replace("$8", "$16")
    .replace("$7", "$15")
    .replace("$6", "$14")
    .replace("$5", "$13")
    .replace("$4", "$12")
    .replace("$3", "$11")
    .replace("$2", "$10")
    .replace("$1,", "$9,")
    + ") SELECT upserted.cdn_path, upserted.media_type, upserted.size_bytes, "
    "(SELECT storage_backend FROM existing) AS prior_backend FROM upserted"
)

ALL_STATEMENTS: dict[str, str] = {
    "RECORD": RECORD,
    "LOCK_CONTENT": LOCK_CONTENT,
    "PREPARE_UPLOAD": PREPARE_UPLOAD,
    "FINISH_UPLOAD": FINISH_UPLOAD,
    "CHECK_UPLOAD": CHECK_UPLOAD,
    "PREFLIGHT_REQUEST": PREFLIGHT_REQUEST,
    "PREFLIGHT_CONTENT": PREFLIGHT_CONTENT,
    "SELECT_CONTENT": SELECT_CONTENT,
    "UPSERT_CONTENT": UPSERT_CONTENT,
    "UPSERT_REQUEST": UPSERT_REQUEST,
}
