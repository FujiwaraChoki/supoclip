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

-- Preserve current minute totals, timestamps and historical failure exclusions.
-- Failures without clips were already refunded. Leave them without an event
-- so an explicit resume checks the allowance before charging them again.
-- Unknown/nonpositive durations previously contributed zero. Leave those tasks
-- uncharged so a resume can measure them before inserting a real charge.
-- Work in UTC to match BillingService, including end-of-month annual anchors.
WITH billing AS (
    SELECT id AS user_id,
           billing_period_start AT TIME ZONE 'UTC' AS period_start,
           billing_period_end AT TIME ZONE 'UTC' AS period_end,
           CURRENT_TIMESTAMP AT TIME ZONE 'UTC' AS now_utc
    FROM users
), anchors AS (
    SELECT *,
           (EXTRACT(YEAR FROM now_utc)::int - EXTRACT(YEAR FROM period_start)::int) * 12
             + EXTRACT(MONTH FROM now_utc)::int - EXTRACT(MONTH FROM period_start)::int AS months
    FROM billing
), anniversaries AS (
    SELECT *, months - CASE
        WHEN period_start + make_interval(months => months) > now_utc THEN 1 ELSE 0
    END AS current_month
    FROM anchors
), windows AS (
    SELECT user_id,
        CASE
            WHEN period_start IS NULL OR period_end IS NULL THEN date_trunc('month', now_utc)
            WHEN period_end - period_start < INTERVAL '33 days' THEN period_start
            ELSE period_start + make_interval(months => current_month)
        END AT TIME ZONE 'UTC' AS window_start,
        CASE
            WHEN period_start IS NULL OR period_end IS NULL
                THEN date_trunc('month', now_utc) + INTERVAL '1 month' - INTERVAL '1 microsecond'
            WHEN period_end - period_start < INTERVAL '33 days' THEN period_end
            ELSE LEAST(period_start + make_interval(months => current_month + 1), period_end)
        END AT TIME ZONE 'UTC' AS window_end
    FROM anniversaries
)
INSERT INTO minute_usage_events (id, user_id, task_id, minutes, source_duration_seconds, created_at)
SELECT t.id, t.user_id, t.id, (t.source_duration_seconds::bigint + 59) / 60,
       t.source_duration_seconds, t.created_at
FROM tasks t JOIN windows w ON w.user_id = t.user_id
WHERE t.created_at >= w.window_start AND t.created_at <= w.window_end
  AND t.source_duration_seconds > 0
  AND (
      COALESCE(t.status, '') NOT IN ('error', 'failed', 'cancelled', 'canceled')
      OR EXISTS (SELECT 1 FROM generated_clips c WHERE c.task_id = t.id)
  )
ON CONFLICT (task_id) DO NOTHING;
