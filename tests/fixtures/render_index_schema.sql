-- Test-only copy of Haruki-Cloud pgstore.go initSQL + pgstore_ddl.go renderIndexDDL.
-- Drawing runtime never executes DDL.

CREATE TABLE IF NOT EXISTS image_cache_entries (
	hash       TEXT PRIMARY KEY,
	group_name TEXT NOT NULL,
	cdn_path   TEXT NOT NULL,
	file_path  TEXT NOT NULL,
	size_bytes BIGINT NOT NULL,
	created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE image_cache_entries ADD COLUMN IF NOT EXISTS storage_backend TEXT;
ALTER TABLE image_cache_entries ADD COLUMN IF NOT EXISTS media_type TEXT;
ALTER TABLE image_cache_entries ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ NULL;
ALTER TABLE image_cache_entries ADD COLUMN IF NOT EXISTS last_referenced_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE image_cache_entries ALTER COLUMN file_path DROP NOT NULL;
UPDATE image_cache_entries SET storage_backend = 'legacy_disk' WHERE storage_backend IS NULL;
CREATE INDEX IF NOT EXISTS idx_ice_group_created ON image_cache_entries (group_name, created_at);
CREATE INDEX IF NOT EXISTS idx_ice_expires ON image_cache_entries (expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ice_last_referenced ON image_cache_entries (last_referenced_at);
CREATE TABLE IF NOT EXISTS render_cache_index (
    request_key  TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL REFERENCES image_cache_entries(hash),
    api_path     TEXT NOT NULL,
    user_id      TEXT NOT NULL DEFAULT 'public',
    group_name   TEXT NOT NULL DEFAULT 'pjsk',
    key_version  INT NOT NULL DEFAULT 3,
    ttl_seconds  BIGINT NOT NULL DEFAULT 0,
    expires_at   TIMESTAMPTZ NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rci_expires ON render_cache_index (expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rci_api_path_user ON render_cache_index (api_path, user_id);
CREATE INDEX IF NOT EXISTS idx_rci_content_hash ON render_cache_index (content_hash);
CREATE TABLE IF NOT EXISTS image_cache_object_deletions (
    content_hash TEXT NOT NULL,
    cdn_path TEXT NOT NULL,
    queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    attempts INT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (content_hash, cdn_path)
);
CREATE INDEX IF NOT EXISTS idx_icod_next_attempt ON image_cache_object_deletions (next_attempt_at);
ALTER TABLE image_cache_entries ADD COLUMN IF NOT EXISTS writer_node TEXT NULL;
ALTER TABLE image_cache_entries ADD COLUMN IF NOT EXISTS written_at TIMESTAMPTZ NULL;
