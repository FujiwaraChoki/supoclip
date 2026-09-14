-- `updated_at` bumps on every write to a clip (reactions, content-policy
-- scans, manual metadata edits -- a table-wide trigger touches it on any
-- UPDATE), so it can't tell a real re-cut apart from an unrelated write and
-- is useless for "did the transcript/clip change since metadata was
-- generated". Snapshot the exact transcript text metadata was generated
-- from instead, then a stale flag is just "current text differs from snapshot".
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS metadata_source_text TEXT;
