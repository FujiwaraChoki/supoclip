-- Digest of the parts a complete request named, recorded before the API asks
-- storage to complete. A retry after a lost response must name the same parts
-- to be told the upload is stored. A different list is a conflict.
ALTER TABLE media_upload_sessions ADD COLUMN IF NOT EXISTS completion_parts_sha256 TEXT
