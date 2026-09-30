"""Pins the render-index SQL text (plan §9.2, addendum A5)."""

from __future__ import annotations

from pathlib import Path
import re

from src.index import sql

ROOT = Path(__file__).resolve().parents[1]

# The canonical schema (plan §9.3, Cloud's renderIndexDDL) — the only columns the statements may name.
REQUEST_COLUMNS = {
    "request_key",
    "content_hash",
    "api_path",
    "user_id",
    "group_name",
    "key_version",
    "ttl_seconds",
    "expires_at",
    "created_at",
    "last_used_at",
}
# Legacy image_cache_entries columns plus the ones the canonical migration adds.
CONTENT_COLUMNS = {
    "hash",
    "group_name",
    "cdn_path",
    "file_path",
    "size_bytes",
    "created_at",
    "storage_backend",
    "media_type",
    "expires_at",
    "last_referenced_at",
    "writer_node",
    "written_at",
}


def _norm(text: str) -> str:
    return " ".join(text.split())


def _select_columns(statement: str) -> list[str]:
    match = re.match(r"SELECT (.*?) FROM", statement)
    assert match is not None
    return [col.strip() for col in match.group(1).split(",")]


def _insert_columns(statement: str) -> list[str]:
    match = re.search(r"\((.*?)\) VALUES", statement)
    assert match is not None
    return [col.strip() for col in match.group(1).split(",")]


def _param_count(statement: str) -> int:
    return len(set(re.findall(r"\$(\d+)", statement)))


def test_no_schema_statements_anywhere_in_src_index() -> None:
    hits = []
    for path in sorted((ROOT / "src" / "index").rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"CREATE|ALTER|DROP|TRUNCATE", line):
                hits.append(f"{path}:{lineno}: {line}")
    assert hits == []


def test_no_update_of_render_cache_index_anywhere_in_src() -> None:
    pattern = re.compile(r"UPDATE\s+render_cache_index", re.IGNORECASE)
    hits = [str(path) for path in (ROOT / "src").rglob("*.py") if pattern.search(path.read_text(encoding="utf-8"))]
    assert hits == []
    for statement in sql.ALL_STATEMENTS.values():
        assert not pattern.search(statement)


def test_statement_set_includes_shared_content_lock() -> None:
    assert set(sql.ALL_STATEMENTS) == {
        "RECORD",
        "RECORD_UPLOAD",
        "LOCK_CONTENT",
        "PREPARE_UPLOAD",
        "PREFLIGHT_REQUEST",
        "PREFLIGHT_CONTENT",
        "SELECT_CONTENT",
        "UPSERT_CONTENT",
        "UPSERT_REQUEST",
    }
    for statement in sql.ALL_STATEMENTS.values():
        assert statement.split(" ", 1)[0] in {"SELECT", "INSERT", "DELETE", "WITH"}


def test_parameter_counts() -> None:
    assert _param_count(sql.PREFLIGHT_REQUEST) == 0
    assert _param_count(sql.PREFLIGHT_CONTENT) == 0
    assert _param_count(sql.SELECT_CONTENT) == 1
    assert _param_count(sql.RECORD) == 16
    assert _param_count(sql.RECORD_UPLOAD) == 17
    assert _param_count(sql.PREPARE_UPLOAD) == 2
    assert _param_count(sql.UPSERT_CONTENT) == 8
    assert sql.LOCK_CONTENT == "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))"
    assert _param_count(sql.UPSERT_REQUEST) == 8


def test_preflight_statements_name_only_canonical_columns() -> None:
    request_cols = _select_columns(sql.PREFLIGHT_REQUEST)
    content_cols = _select_columns(sql.PREFLIGHT_CONTENT)
    assert set(request_cols) == REQUEST_COLUMNS
    assert set(content_cols) <= CONTENT_COLUMNS
    assert "last_referenced_at" in content_cols
    assert sql.PREFLIGHT_REQUEST.endswith("FROM render_cache_index LIMIT 0")
    assert sql.PREFLIGHT_CONTENT.endswith("FROM image_cache_entries LIMIT 0")


def test_select_content_matches_content_row_fields() -> None:
    from dataclasses import fields

    from src.index.protocols import ContentRow

    assert _select_columns(sql.SELECT_CONTENT) == [f.name for f in fields(ContentRow)]
    assert set(_select_columns(sql.SELECT_CONTENT)) <= CONTENT_COLUMNS
    assert _norm(sql.SELECT_CONTENT).endswith("FROM image_cache_entries WHERE hash = $1")


def test_upsert_content_text_is_pinned() -> None:
    expected = (
        "INSERT INTO image_cache_entries (hash, group_name, cdn_path, file_path, size_bytes, storage_backend, "
        "media_type, expires_at, last_referenced_at, created_at, writer_node, written_at) "
        "VALUES ($1, $2, $3, NULL, $4, 'garage', $5, $6, "
        "now(), now(), $7, $8) ON CONFLICT (hash) DO UPDATE SET "
        "cdn_path = CASE WHEN image_cache_entries.storage_backend IS DISTINCT FROM 'garage' THEN EXCLUDED.cdn_path "
        "ELSE image_cache_entries.cdn_path END, "
        "size_bytes = CASE WHEN image_cache_entries.storage_backend IS DISTINCT FROM 'garage' "
        "THEN EXCLUDED.size_bytes ELSE image_cache_entries.size_bytes END, "
        "media_type = CASE WHEN image_cache_entries.storage_backend IS DISTINCT FROM 'garage' "
        "THEN EXCLUDED.media_type ELSE image_cache_entries.media_type END, "
        "writer_node = CASE WHEN image_cache_entries.storage_backend IS DISTINCT FROM 'garage' "
        "THEN EXCLUDED.writer_node ELSE image_cache_entries.writer_node END, "
        "written_at = CASE WHEN image_cache_entries.storage_backend IS DISTINCT FROM 'garage' "
        "THEN EXCLUDED.written_at ELSE image_cache_entries.written_at END, "
        "storage_backend = 'garage', file_path = NULL, last_referenced_at = now(), "
        "expires_at = CASE WHEN image_cache_entries.expires_at IS NULL OR EXCLUDED.expires_at IS NULL THEN NULL "
        "ELSE GREATEST(image_cache_entries.expires_at, EXCLUDED.expires_at) END"
    )
    assert _norm(sql.UPSERT_CONTENT) == _norm(expected)


def test_upsert_content_guards_per_column_without_statement_where() -> None:
    text = _norm(sql.UPSERT_CONTENT)
    conflict_tail = text.split("DO UPDATE SET", 1)[1]
    assert "WHERE" not in conflict_tail
    for column in ("cdn_path", "size_bytes", "media_type"):
        assert (
            f"{column} = CASE WHEN image_cache_entries.storage_backend IS DISTINCT FROM 'garage' "
            f"THEN EXCLUDED.{column} ELSE image_cache_entries.{column} END"
        ) in text
    assert "last_referenced_at = now()," in conflict_tail
    assert "GREATEST(image_cache_entries.expires_at, EXCLUDED.expires_at)" in conflict_tail
    assert "image_cache_entries.expires_at IS NULL OR EXCLUDED.expires_at IS NULL THEN NULL" in conflict_tail
    assert set(_insert_columns(sql.UPSERT_CONTENT)) <= CONTENT_COLUMNS


def test_upsert_request_lists_all_ten_columns() -> None:
    columns = _insert_columns(sql.UPSERT_REQUEST)
    assert len(columns) == 10
    assert set(columns) == REQUEST_COLUMNS
    assert "created_at" in columns
    text = _norm(sql.UPSERT_REQUEST)
    assert "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now(), now())" in text
    assert text.endswith("last_used_at = now()")


# Cloud's statements (utils/imagecache/pgstore_outbox.go, pgstore_lifecycle.go) that Drawing's folded ones embed.
CLOUD_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))"
CLOUD_VERIFY_PREDICATE = "attempts = 0 AND next_attempt_at > clock_timestamp() FOR UPDATE"
CLOUD_REGISTER_VALUES = "now() + interval '5 minutes'"


def test_begin_and_lock_is_clouds_lock_in_one_read_committed_simple_query() -> None:
    digest = "0123456789abcdef" * 4
    assert sql.LOCK_CONTENT == CLOUD_LOCK
    assert sql.begin_and_lock(digest) == (
        f"BEGIN ISOLATION LEVEL READ COMMITTED; SELECT pg_advisory_xact_lock(hashtextextended('{digest}', 0))"
    )
    for bad in ("AB" * 32, "ab" * 31, "ab" * 33, "", "x'; DROP TABLE t; --" + "a" * 44):
        try:
            sql.begin_and_lock(bad)
        except ValueError:
            continue
        raise AssertionError(f"inlined {bad!r}")


def test_prepare_upload_registers_clouds_intent_only_on_a_miss() -> None:
    text = _norm(sql.PREPARE_UPLOAD)
    assert "INSERT INTO image_cache_object_deletions (content_hash, cdn_path, next_attempt_at)" in text
    assert f"SELECT $1, $2, {CLOUD_REGISTER_VALUES} WHERE NOT EXISTS (SELECT 1 FROM existing)" in text
    assert "ON CONFLICT (content_hash, cdn_path) DO NOTHING" in text
    assert "FROM image_cache_entries WHERE hash = $1 AND storage_backend = 'garage'" in text


def test_record_upload_is_record_gated_on_the_intent_plus_finish_and_requeue() -> None:
    text = _norm(sql.RECORD_UPLOAD)
    # The intent check is Cloud's verifyUploadSQL on the candidate path.
    assert text.startswith(
        "WITH intent AS (SELECT 1 FROM image_cache_object_deletions WHERE content_hash = $1 AND cdn_path = $3 AND "
        + CLOUD_VERIFY_PREDICATE
        + ")"
    )
    # The content upsert is UPSERT_CONTENT's column list, values and conflict rules, gated on ownership.
    insert, conflict = _norm(sql.UPSERT_CONTENT).split(" VALUES (", 1)
    values, conflict = conflict.split(") ", 1)
    assert (
        f"{insert} SELECT {values} WHERE EXISTS (SELECT 1 FROM intent) OR EXISTS "
        f"(SELECT 1 FROM existing WHERE storage_backend = 'garage') {conflict} RETURNING"
    ) in text
    # The request upsert is UPSERT_REQUEST shifted by eight parameters, and runs only after a content row.
    insert, rest = _norm(sql.UPSERT_REQUEST).split(" VALUES (", 1)
    values, conflict = rest.split(") ", 1)
    shifted = re.sub(r"\$(\d+)", lambda m: f"${int(m[1]) + 8}", values)
    assert f"{insert} SELECT {shifted} FROM upserted {conflict})" in text
    # Cloud's finishObjectDeleteSQL, only once the candidate path is the recorded one.
    assert (
        "finished AS (DELETE FROM image_cache_object_deletions WHERE content_hash = $1 AND cdn_path = $3 "
        "AND EXISTS (SELECT 1 FROM upserted WHERE upserted.cdn_path = $3))"
    ) in text
    # An uploaded ($17) but unrecorded candidate is queued again, due now (the column default).
    assert (
        "requeued AS (INSERT INTO image_cache_object_deletions (content_hash, cdn_path) SELECT $1, $3 "
        "WHERE $17 AND NOT EXISTS (SELECT 1 FROM upserted WHERE upserted.cdn_path = $3) "
        "ON CONFLICT (content_hash, cdn_path) DO NOTHING)"
    ) in text
    assert text.endswith("(SELECT storage_backend FROM existing) AS prior_backend FROM upserted")
