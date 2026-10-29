-- Direct uploads remember a content hash so a retry of the same file reuses
-- the stored object. The remote copy, when object storage is on, is recorded
-- after the HTTP response so the request does not wait for it.

ALTER TABLE media_upload_sessions ADD COLUMN IF NOT EXISTS content_sha256 TEXT;
ALTER TABLE media_upload_sessions ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
ALTER TABLE media_upload_sessions ADD COLUMN IF NOT EXISTS storage_reference TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS media_upload_sessions_content_hash_idx
    ON media_upload_sessions (user_id, content_sha256)
    WHERE content_sha256 IS NOT NULL AND state = 'completed';

CREATE UNIQUE INDEX IF NOT EXISTS media_upload_sessions_idempotency_idx
    ON media_upload_sessions (user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL AND state = 'completed';
