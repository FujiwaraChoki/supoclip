-- Fail startup transaction safely instead of waiting indefinitely on live writes.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

-- Feedback inbox (docs/feedback.md): every in-app feedback, bug, sales and cancellation
-- message is stored here, triaged in /admin/feedback, and fanned out to admin-managed
-- webhooks through a durable outbox. Read and written by backend/src/feedback/.
--
-- feedback_submissions: one row per message. user_id is NULL for anonymous senders and
-- after the account is deleted (the email snapshot is cleared then too).
CREATE TABLE IF NOT EXISTS feedback_submissions (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    email VARCHAR(255),
    category VARCHAR(20) NOT NULL CHECK (category IN ('bug', 'feature', 'general', 'sales', 'cancellation')),
    message TEXT NOT NULL,
    page_url TEXT,
    platform VARCHAR(10) NOT NULL DEFAULT 'web' CHECK (platform IN ('web', 'ios')),
    user_agent TEXT,
    app_version VARCHAR(50),
    locale VARCHAR(20),
    status VARCHAR(20) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'resolved')),
    resolved_at TIMESTAMPTZ,
    resolved_by VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_feedback_submissions_created ON feedback_submissions (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_submissions_status ON feedback_submissions (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_submissions_category ON feedback_submissions (category, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_submissions_user ON feedback_submissions (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_submissions_email ON feedback_submissions (LOWER(email));

-- Admin notes on a submission, oldest first.
CREATE TABLE IF NOT EXISTS feedback_notes (
    id VARCHAR(36) PRIMARY KEY,
    submission_id VARCHAR(36) NOT NULL REFERENCES feedback_submissions(id) ON DELETE CASCADE,
    author_id VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_feedback_notes_submission ON feedback_notes (submission_id, created_at);

-- Webhook destinations, managed in /admin/feedback/webhooks. The URL (it carries the
-- Discord token) and the signing secret are encrypted with APP_SETTINGS_ENCRYPTION_KEY,
-- url_hint (host and last characters) is all the admin UI ever shows.
-- env_source names the environment variable a row was imported from.
CREATE TABLE IF NOT EXISTS feedback_webhooks (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(80) NOT NULL,
    kind VARCHAR(10) NOT NULL CHECK (kind IN ('discord', 'json')),
    url_encrypted TEXT NOT NULL,
    url_hint VARCHAR(160) NOT NULL,
    secret_encrypted TEXT,
    categories TEXT[] NOT NULL DEFAULT '{}',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    env_source VARCHAR(64),
    created_by VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    updated_by VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Environment webhooks (DISCORD_FEEDBACK_WEBHOOK_URL, DISCORD_SALES_WEBHOOK_URL) are
-- copied into feedback_webhooks once, on the first API start after this migration.
-- A row here means that copy happened, so deleting the webhook never brings it back.
CREATE TABLE IF NOT EXISTS feedback_webhook_env_imports (
    env_key VARCHAR(64) PRIMARY KEY,
    webhook_id VARCHAR(36),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Outbox: one delivery per (submission, webhook), written in the submission's
-- transaction. The id is sent as the delivery id, so receivers can deduplicate.
--   pending    waiting for its next attempt (lease_expires_at while a sender holds it)
--   delivered  the receiver answered 2xx
--   failed     out of attempts, or a permanent 4xx (an admin can retry it)
--   cancelled  the webhook was disabled before it went out
-- Test sends from the admin UI are recorded with submission_id NULL and is_test.
CREATE TABLE IF NOT EXISTS feedback_webhook_deliveries (
    id VARCHAR(36) PRIMARY KEY,
    submission_id VARCHAR(36) REFERENCES feedback_submissions(id) ON DELETE CASCADE,
    webhook_id VARCHAR(36) NOT NULL REFERENCES feedback_webhooks(id) ON DELETE CASCADE,
    is_test BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'delivered', 'failed', 'cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_expires_at TIMESTAMPTZ,
    last_status_code INTEGER,
    last_error TEXT,
    delivered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_feedback_webhook_deliveries_submission
    ON feedback_webhook_deliveries (submission_id, webhook_id) WHERE submission_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_feedback_webhook_deliveries_due
    ON feedback_webhook_deliveries (next_attempt_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_feedback_webhook_deliveries_webhook
    ON feedback_webhook_deliveries (webhook_id, created_at DESC);

-- Every attempt, for the delivery log in the admin UI.
CREATE TABLE IF NOT EXISTS feedback_webhook_attempts (
    id BIGSERIAL PRIMARY KEY,
    delivery_id VARCHAR(36) NOT NULL REFERENCES feedback_webhook_deliveries(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL,
    status_code INTEGER,
    error TEXT,
    duration_ms INTEGER,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_feedback_webhook_attempts_delivery ON feedback_webhook_attempts (delivery_id, attempted_at)
;

-- Cancellation notes were the only feedback stored before this. Copy them in (same ids,
-- so applying this again is a no-op). They already reached Discord, so they arrive
-- resolved and get no deliveries.
INSERT INTO feedback_submissions (id, user_id, email, category, message, status, resolved_at, created_at, updated_at)
SELECT n.id, u.id, u.email, 'cancellation', n.message, 'resolved', n.created_at, n.created_at, n.created_at
FROM cancellation_notes n
LEFT JOIN users u ON u.id = n.user_id
ON CONFLICT (id) DO NOTHING;

SET LOCAL lock_timeout = DEFAULT;
SET LOCAL statement_timeout = DEFAULT;
