-- Option to disable AI-written hook titles per task.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS include_hook_titles BOOLEAN NOT NULL DEFAULT TRUE;