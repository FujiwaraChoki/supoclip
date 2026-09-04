ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS stage_progress INTEGER DEFAULT 0 CHECK (stage_progress >= 0 AND stage_progress <= 100);

UPDATE tasks SET stage_progress = 0 WHERE stage_progress IS NULL;
