-- Queued platform-preset exports. The synchronous GET export route stays;
-- these rows back the non-blocking start/status/download routes.
CREATE TABLE IF NOT EXISTS clip_exports (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    clip_id VARCHAR(36) NOT NULL REFERENCES generated_clips(id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    preset VARCHAR(32) NOT NULL,
    options_hash VARCHAR(64) NOT NULL,
    source_file_path TEXT NOT NULL,
    status VARCHAR(16) NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    error_message TEXT,
    file_path TEXT,
    download_filename VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT clip_exports_status_check CHECK (status IN ('queued', 'running', 'succeeded', 'failed'))
);

-- One live encode per clip and option set. Failed rows stay so a retry can insert again.
CREATE UNIQUE INDEX IF NOT EXISTS ux_clip_exports_live_options
    ON clip_exports (clip_id, options_hash)
    WHERE status IN ('queued', 'running', 'succeeded');

CREATE INDEX IF NOT EXISTS ix_clip_exports_clip_id ON clip_exports (clip_id);

CREATE INDEX IF NOT EXISTS ix_clip_exports_task_id ON clip_exports (task_id);

CREATE INDEX IF NOT EXISTS ix_clip_exports_expires_at
    ON clip_exports (expires_at)
    WHERE status = 'succeeded';

CREATE INDEX IF NOT EXISTS ix_clip_exports_stale
    ON clip_exports (updated_at)
    WHERE status IN ('queued', 'running');
