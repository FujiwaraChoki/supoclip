-- No task/clip foreign keys: deleting a clip must not restore its AI allowance.
-- Existing requests are not backfilled. Monthly metering starts on rollout.
CREATE TABLE IF NOT EXISTS ai_edit_usage_events (
    job_id VARCHAR(32) PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    refunded_at TIMESTAMP WITH TIME ZONE
);
CREATE INDEX IF NOT EXISTS ix_ai_edit_usage_events_user_created
    ON ai_edit_usage_events (user_id, created_at);
