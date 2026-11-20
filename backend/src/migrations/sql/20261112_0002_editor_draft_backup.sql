-- Rebuilding a clip studio session prepares its source video again and starts
-- a fresh draft. The draft it replaces is kept here so the user can download it.
ALTER TABLE editor_sessions ADD COLUMN IF NOT EXISTS draft_backup JSONB;
