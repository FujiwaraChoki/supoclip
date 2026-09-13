-- Soft delete for tasks. Deleting a task now sets deleted_at instead of
-- hard-deleting the row (and its generated_clips, which the task still owns
-- while it sits in trash) so users can restore accidentally-deleted tasks.
-- A separate purge path does the real hard delete.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_deleted_at ON tasks(deleted_at);
