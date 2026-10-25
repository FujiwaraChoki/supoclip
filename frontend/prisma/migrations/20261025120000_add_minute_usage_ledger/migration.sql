-- Charges survive task deletion. Only failure refunds and checked retries
-- change their billing state. IDs deliberately have no foreign keys, so task or
-- source deletion cannot erase the charge or its task idempotency key.
CREATE TABLE IF NOT EXISTS minute_usage_events (
    id VARCHAR(36) PRIMARY KEY,
    user_id TEXT NOT NULL,
    task_id TEXT UNIQUE,
    minutes INTEGER NOT NULL CHECK (minutes > 0),
    source_duration_seconds INTEGER NOT NULL CHECK (source_duration_seconds > 0),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    refunded_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT minute_usage_events_rounding_check
        CHECK (minutes = (source_duration_seconds::bigint + 59) / 60)
);
CREATE INDEX IF NOT EXISTS ix_minute_usage_events_user_created
    ON minute_usage_events (user_id, created_at);

-- The backend migration owns the current-period backfill because its failure
-- exclusions query generated_clips, which Prisma does not create. It runs
-- before the new backend serves any billing summaries or accepts tasks.
