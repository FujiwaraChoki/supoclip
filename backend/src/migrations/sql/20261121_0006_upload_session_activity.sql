-- When an open multipart upload last showed it was alive: a part URL refresh,
-- a finished part reported by the browser, or a recent part the cleanup worker
-- found in storage. NULL means created_at. An open session quiet for longer
-- than MEDIA_UPLOAD_STALE_SECONDS stops holding an upload slot and is aborted.
-- Undo: DROP INDEX media_upload_sessions_open_idx; ALTER TABLE media_upload_sessions DROP COLUMN last_activity_at;
ALTER TABLE media_upload_sessions ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMPTZ;

-- Only open sessions, so it stays tiny; the cleanup worker scans these.
CREATE INDEX IF NOT EXISTS media_upload_sessions_open_idx ON media_upload_sessions (expires_at)
    WHERE state IN ('reserved', 'uploading');
