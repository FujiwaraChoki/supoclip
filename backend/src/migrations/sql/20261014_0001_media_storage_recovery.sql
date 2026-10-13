ALTER TABLE media_deletions ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE media_deletions ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE media_deletions ADD COLUMN IF NOT EXISTS hold_token TEXT;
CREATE INDEX IF NOT EXISTS media_deletions_due_idx ON media_deletions (next_attempt_at) WHERE hold_token IS NULL;
CREATE INDEX IF NOT EXISTS generated_clips_file_path_idx ON generated_clips (file_path);
CREATE INDEX IF NOT EXISTS sources_url_idx ON sources (url);

CREATE TABLE IF NOT EXISTS media_upload_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    upload_id TEXT UNIQUE,
    video_path TEXT UNIQUE,
    size BIGINT NOT NULL CHECK (size > 0),
    state TEXT NOT NULL DEFAULT 'reserved' CHECK (state IN ('reserved', 'uploading', 'completed', 'aborted')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '1 day'
);
CREATE INDEX IF NOT EXISTS media_upload_sessions_owner_idx ON media_upload_sessions (user_id, created_at);
