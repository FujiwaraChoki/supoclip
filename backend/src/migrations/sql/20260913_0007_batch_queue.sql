-- Batch processing queue: drop multiple videos, process sequentially,
-- survives an app/backend restart (state lives here, not in browser memory
-- or localStorage). `template_id` is the applied preset — reuses the
-- existing project_templates system rather than inventing a second
-- "preset" concept.
CREATE TABLE IF NOT EXISTS batch_queues (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    template_id VARCHAR(36) REFERENCES project_templates(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    auto_export_to_source BOOLEAN NOT NULL DEFAULT FALSE,
    delay_between_items_seconds INTEGER NOT NULL DEFAULT 3,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_batch_queues_user_id ON batch_queues(user_id);
CREATE INDEX IF NOT EXISTS idx_batch_queues_status ON batch_queues(status);

CREATE TABLE IF NOT EXISTS batch_queue_items (
    id VARCHAR(36) PRIMARY KEY,
    batch_queue_id VARCHAR(36) NOT NULL REFERENCES batch_queues(id) ON DELETE CASCADE,
    item_order INTEGER NOT NULL,
    source_filename VARCHAR(500) NOT NULL,
    source_path VARCHAR(1000),
    task_id VARCHAR(36) REFERENCES tasks(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    progress_percent INTEGER NOT NULL DEFAULT 0,
    current_stage VARCHAR(30),
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_batch_queue_items_queue_id ON batch_queue_items(batch_queue_id, item_order);
