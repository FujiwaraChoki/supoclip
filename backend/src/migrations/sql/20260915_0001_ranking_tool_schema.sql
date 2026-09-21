-- Ranking/Compilation tool: adds a task_type discriminator so the existing
-- tasks/list/trash/soft-delete machinery can host a second project type
-- alongside clipping, a ranking_settings JSON blob (same TEXT-JSON convention
-- as generated_clips.reactions/hook_title_variants) for template/overlay/
-- export prefs, and ranking_inputs for the N source videos a ranking project
-- holds (clipping's tasks/generated_clips model assumes exactly one source,
-- the opposite cardinality of ranking's N-sources-to-one-output). The
-- rendered compilation output itself reuses generated_clips as a single row
-- (clip_order = 0) rather than a parallel output table.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS task_type VARCHAR(20) NOT NULL DEFAULT 'clipping' CHECK (task_type IN ('clipping', 'ranking'));
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS ranking_settings TEXT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_task_type ON tasks(task_type);

CREATE TABLE IF NOT EXISTS ranking_inputs (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    task_id VARCHAR(36) NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    file_path VARCHAR(500) NOT NULL,
    original_filename VARCHAR(500) NOT NULL,
    duration_seconds FLOAT,
    order_index INTEGER NOT NULL,
    rank_position INTEGER NULL,
    thumbnail_path VARCHAR(500) NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ranking_inputs_task_id ON ranking_inputs(task_id, order_index);
