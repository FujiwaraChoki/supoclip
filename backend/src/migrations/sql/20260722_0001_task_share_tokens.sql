ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS share_token VARCHAR(64);

ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS share_enabled BOOLEAN NOT NULL DEFAULT false;

CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_share_token
ON tasks(share_token)
WHERE share_token IS NOT NULL;
