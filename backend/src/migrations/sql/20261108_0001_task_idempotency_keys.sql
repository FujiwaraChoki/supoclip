-- Idempotency-Key for task creation. A key names one create request for its
-- user, and a repeat within the retention window returns the task it made
-- instead of a second charged one. The row is inserted in the task's own
-- transaction. task_id has no foreign key: like minute_usage_events, deleting
-- the task must not free the key and let a late retry charge again.
CREATE TABLE IF NOT EXISTS task_idempotency_keys (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    task_id TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, idempotency_key)
)
