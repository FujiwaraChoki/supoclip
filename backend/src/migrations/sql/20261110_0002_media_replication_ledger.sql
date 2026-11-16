-- Replication outbox for the two media object stores, shared by every host.
-- One row per object key that a store has not acknowledged yet. A newer write or
-- delete replaces the row, and op_id tells a late acknowledgement which revision it was for.
-- Read and written by backend/src/media_replication_ledger.py.
CREATE TABLE IF NOT EXISTS media_replication_ops (
    object_key TEXT PRIMARY KEY,
    operation TEXT NOT NULL CHECK (operation IN ('upload', 'copy', 'delete')),
    source_store TEXT,
    pending TEXT[] NOT NULL,
    sha256 TEXT,
    size BIGINT,
    extra JSONB NOT NULL DEFAULT '{}',
    op_id TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_media_replication_ops_next_attempt ON media_replication_ops (next_attempt_at);
-- The latest storage probe, written by the cron worker and read by /health/storage on any host.
CREATE TABLE IF NOT EXISTS media_storage_health (
    id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    report JSONB NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
