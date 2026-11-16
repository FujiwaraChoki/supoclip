-- Rows that name a path on a local disk record which disk, so a process on
-- another machine does not act on them.

-- A queued deletion of a local path belongs to the disk it was queued on.
-- NULL is an object storage reference, or a row from before this column.
-- The key stays file_path alone, so the previous release still runs on this schema.
ALTER TABLE media_deletions ADD COLUMN IF NOT EXISTS local_volume VARCHAR(64);

-- The disk holding an upload that has not been copied to object storage yet.
ALTER TABLE media_upload_sessions ADD COLUMN IF NOT EXISTS staging_volume VARCHAR(64);

CREATE INDEX IF NOT EXISTS media_upload_sessions_uncopied_idx ON media_upload_sessions (created_at)
    WHERE state = 'completed' AND storage_reference IS NULL
