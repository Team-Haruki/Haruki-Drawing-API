"""The three SQL statements Drawing runs against the render index (plan §9.2, addendum A5), pinned as text.

SELECT/INSERT only. Every column named here is one Haruki-Cloud's canonical schema text ships; Drawing never
ships or runs schema changes, and never touches `render_cache_index.last_used_at` after the insert (Cloud
bumps it on lookup).

`RECORD` is the whole index write in ONE statement, so it is atomic without BEGIN/COMMIT and costs one round
trip. Its two data-modifying CTEs are the former `UPSERT_CONTENT` and `UPSERT_REQUEST` verbatim (only the
request's parameters are renumbered `$7..$14`), so the rows it leaves behind are the ones the two-statement
transaction left: the same columns, the same values, one `now()` for both rows, the same conflict rules. The
`render_cache_index.content_hash` foreign key is checked at the end of the statement, where the content row
the statement itself inserted is visible.

The content upsert guards only the backend-shaped columns (`cdn_path`, `size_bytes`, `media_type`) per column:
an existing `legacy_disk` row is upgraded, an existing `garage` row keeps its path forever (the reuse rule of
addendum A2), and `last_referenced_at` plus the NULL-is-infinite `expires_at` merge run on every upsert.
A statement-level guard would freeze the retention clock of `garage` rows — do not "simplify" it.

`RECORD` returns the stored row's `cdn_path`, `media_type` and `size_bytes` (after the upsert, i.e. a
pre-existing `garage` row's own values) and the `storage_backend` the row had before the statement
(`prior_backend`, NULL when the hash was new). That replaces the pre-upload content lookup: the ref carries the
stored path, and `prior_backend = 'garage'` is a dedup hit (`reused`).
"""

PREFLIGHT_REQUEST = (
    "SELECT request_key, content_hash, api_path, user_id, group_name, key_version, "
    "ttl_seconds, expires_at, created_at, last_used_at FROM render_cache_index LIMIT 0"
)

PREFLIGHT_CONTENT = (
    "SELECT hash, group_name, cdn_path, file_path, size_bytes, storage_backend, media_type, "
    "expires_at, last_referenced_at FROM image_cache_entries LIMIT 0"
)

RECORD = (
    "WITH existing AS (SELECT storage_backend FROM image_cache_entries WHERE hash = $1), "
    "upserted AS ("
    "INSERT INTO image_cache_entries "
    "(hash, group_name, cdn_path, file_path, size_bytes, storage_backend, media_type, expires_at, "
    "last_referenced_at, created_at) "
    "VALUES ($1, $2, $3, NULL, $4, 'garage', $5, $6, now(), now()) "
    "ON CONFLICT (hash) DO UPDATE SET "
    "cdn_path = CASE WHEN image_cache_entries.storage_backend IS DISTINCT FROM 'garage' "
    "THEN EXCLUDED.cdn_path ELSE image_cache_entries.cdn_path END, "
    "size_bytes = CASE WHEN image_cache_entries.storage_backend IS DISTINCT FROM 'garage' "
    "THEN EXCLUDED.size_bytes ELSE image_cache_entries.size_bytes END, "
    "media_type = CASE WHEN image_cache_entries.storage_backend IS DISTINCT FROM 'garage' "
    "THEN EXCLUDED.media_type ELSE image_cache_entries.media_type END, "
    "storage_backend = 'garage', "
    "file_path = NULL, "
    "last_referenced_at = now(), "
    "expires_at = CASE "
    "WHEN image_cache_entries.expires_at IS NULL OR EXCLUDED.expires_at IS NULL THEN NULL "
    "ELSE GREATEST(image_cache_entries.expires_at, EXCLUDED.expires_at) END "
    "RETURNING cdn_path, media_type, size_bytes), "
    "indexed AS ("
    "INSERT INTO render_cache_index "
    "(request_key, content_hash, api_path, user_id, group_name, key_version, ttl_seconds, expires_at, "
    "created_at, last_used_at) "
    "VALUES ($7, $8, $9, $10, $11, $12, $13, $14, now(), now()) "
    "ON CONFLICT (request_key) DO UPDATE SET "
    "content_hash = EXCLUDED.content_hash, api_path = EXCLUDED.api_path, user_id = EXCLUDED.user_id, "
    "group_name = EXCLUDED.group_name, key_version = EXCLUDED.key_version, "
    "ttl_seconds = EXCLUDED.ttl_seconds, expires_at = EXCLUDED.expires_at, last_used_at = now()) "
    "SELECT upserted.cdn_path, upserted.media_type, upserted.size_bytes, "
    "(SELECT storage_backend FROM existing) AS prior_backend FROM upserted"
)

ALL_STATEMENTS: dict[str, str] = {
    "PREFLIGHT_REQUEST": PREFLIGHT_REQUEST,
    "PREFLIGHT_CONTENT": PREFLIGHT_CONTENT,
    "RECORD": RECORD,
}
