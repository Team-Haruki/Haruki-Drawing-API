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


# The two statements the index write used to run inside BEGIN/COMMIT (3.4.0), pinned verbatim. `RECORD` must keep
# them byte for byte (request parameters renumbered), so the rows Cloud reads cannot drift.
FORMER_UPSERT_CONTENT = (
    "INSERT INTO image_cache_entries (hash, group_name, cdn_path, file_path, size_bytes, storage_backend, "
    "media_type, expires_at, last_referenced_at, created_at) VALUES ($1, $2, $3, NULL, $4, 'garage', $5, $6, "
    "now(), now()) ON CONFLICT (hash) DO UPDATE SET "
    "cdn_path = CASE WHEN image_cache_entries.storage_backend IS DISTINCT FROM 'garage' THEN EXCLUDED.cdn_path "
    "ELSE image_cache_entries.cdn_path END, "
    "size_bytes = CASE WHEN image_cache_entries.storage_backend IS DISTINCT FROM 'garage' "
    "THEN EXCLUDED.size_bytes ELSE image_cache_entries.size_bytes END, "
    "media_type = CASE WHEN image_cache_entries.storage_backend IS DISTINCT FROM 'garage' "
    "THEN EXCLUDED.media_type ELSE image_cache_entries.media_type END, "
    "storage_backend = 'garage', file_path = NULL, last_referenced_at = now(), "
    "expires_at = CASE WHEN image_cache_entries.expires_at IS NULL OR EXCLUDED.expires_at IS NULL THEN NULL "
    "ELSE GREATEST(image_cache_entries.expires_at, EXCLUDED.expires_at) END"
)
FORMER_UPSERT_REQUEST = (
    "INSERT INTO render_cache_index (request_key, content_hash, api_path, user_id, group_name, key_version, "
    "ttl_seconds, expires_at, created_at, last_used_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now(), now()) "
    "ON CONFLICT (request_key) DO UPDATE SET content_hash = EXCLUDED.content_hash, api_path = EXCLUDED.api_path, "
    "user_id = EXCLUDED.user_id, group_name = EXCLUDED.group_name, key_version = EXCLUDED.key_version, "
    "ttl_seconds = EXCLUDED.ttl_seconds, expires_at = EXCLUDED.expires_at, last_used_at = now()"
)


def _record_parts() -> tuple[str, str, str, str]:
    """(existing CTE, content upsert, request upsert, final SELECT) of `RECORD`."""
    match = re.fullmatch(
        r"WITH existing AS \((.*?)\), upserted AS \((.*?) RETURNING cdn_path, media_type, size_bytes\), "
        r"indexed AS \((.*?)\) (SELECT .*)",
        _norm(sql.RECORD),
    )
    assert match is not None
    return match.group(1), match.group(2), match.group(3), match.group(4)


def _shift_params(statement: str, offset: int) -> str:
    return re.sub(r"\$(\d+)", lambda m: f"${int(m.group(1)) + offset}", statement)


def test_statement_set_is_exactly_three() -> None:
    assert set(sql.ALL_STATEMENTS) == {"PREFLIGHT_REQUEST", "PREFLIGHT_CONTENT", "RECORD"}
    for statement in sql.ALL_STATEMENTS.values():
        assert statement.split(" ", 1)[0] in {"SELECT", "WITH"}
    assert not re.search(r"\b(BEGIN|COMMIT|ROLLBACK|DELETE)\b", sql.RECORD)


def test_parameter_counts() -> None:
    assert _param_count(sql.PREFLIGHT_REQUEST) == 0
    assert _param_count(sql.PREFLIGHT_CONTENT) == 0
    assert _param_count(sql.RECORD) == 14
    assert sorted({int(n) for n in re.findall(r"\$(\d+)", sql.RECORD)}) == list(range(1, 15))


def test_preflight_statements_name_only_canonical_columns() -> None:
    request_cols = _select_columns(sql.PREFLIGHT_REQUEST)
    content_cols = _select_columns(sql.PREFLIGHT_CONTENT)
    assert set(request_cols) == REQUEST_COLUMNS
    assert set(content_cols) <= CONTENT_COLUMNS
    assert "last_referenced_at" in content_cols
    assert sql.PREFLIGHT_REQUEST.endswith("FROM render_cache_index LIMIT 0")
    assert sql.PREFLIGHT_CONTENT.endswith("FROM image_cache_entries LIMIT 0")


def test_record_content_upsert_is_the_former_statement_verbatim() -> None:
    _, content, _, _ = _record_parts()
    assert content == _norm(FORMER_UPSERT_CONTENT)


def test_record_request_upsert_is_the_former_statement_renumbered() -> None:
    _, _, request, _ = _record_parts()
    assert request == _norm(_shift_params(FORMER_UPSERT_REQUEST, 6))


def test_record_reads_the_prior_backend_and_returns_the_stored_row() -> None:
    from dataclasses import fields

    from src.index.protocols import RecordResult

    existing, _, _, final = _record_parts()
    assert existing == "SELECT storage_backend FROM image_cache_entries WHERE hash = $1"
    assert final == (
        "SELECT upserted.cdn_path, upserted.media_type, upserted.size_bytes, "
        "(SELECT storage_backend FROM existing) AS prior_backend FROM upserted"
    )
    returned = ["cdn_path", "media_type", "size_bytes", "prior_backend"]
    assert [f.name for f in fields(RecordResult)][:4] == returned


def test_record_content_upsert_guards_per_column_without_statement_where() -> None:
    _, text, _, _ = _record_parts()
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
    assert set(_insert_columns(text)) <= CONTENT_COLUMNS


def test_record_request_upsert_lists_all_ten_columns() -> None:
    _, _, text, _ = _record_parts()
    columns = _insert_columns(text)
    assert len(columns) == 10
    assert set(columns) == REQUEST_COLUMNS
    assert "created_at" in columns
    assert "VALUES ($7, $8, $9, $10, $11, $12, $13, $14, now(), now())" in text
    assert text.endswith("last_used_at = now()")
