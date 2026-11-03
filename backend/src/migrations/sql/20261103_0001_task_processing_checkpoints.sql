-- Task-owned checkpoints survive failed attempts and disappear with the task.
CREATE TABLE IF NOT EXISTS task_processing_checkpoints (
    task_id VARCHAR(36) PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    input_key VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
