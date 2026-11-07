-- Workflows: what a task makes (highlights, series, topic, recap, trailer)
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS workflow VARCHAR(20) NOT NULL DEFAULT 'highlights';
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_workflow_check;
ALTER TABLE tasks ADD CONSTRAINT tasks_workflow_check
    CHECK (workflow IN ('highlights', 'series', 'topic', 'recap', 'trailer'));
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS workflow_options JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS workflow_plan JSONB;
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS workflow_role VARCHAR(20);
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS part_number INTEGER;
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS workflow_meta JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS idx_tasks_user_workflow ON tasks(user_id, workflow)
