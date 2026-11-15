-- Cancellation reviews: every Stripe subscription cancellation is investigated
-- and reported to the operator chat. Read and written by backend/src/cancellation_reviews/.
--
-- stripe_cancellation_events: signed Stripe events, trimmed to the subscription
-- fields a review needs, stored once per (account, mode, event). The cron worker
-- folds them into reviews in event order.
CREATE TABLE IF NOT EXISTS stripe_cancellation_events (
    id VARCHAR(255) PRIMARY KEY,
    account_id VARCHAR(255) NOT NULL,
    livemode BOOLEAN NOT NULL,
    event_id VARCHAR(255) NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    event_created TIMESTAMPTZ NOT NULL,
    subscription_id VARCHAR(255) NOT NULL,
    customer_id VARCHAR(255) NOT NULL,
    snapshot JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    folded_at TIMESTAMPTZ,
    fold_attempts INTEGER NOT NULL DEFAULT 0,
    fold_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_stripe_cancellation_events_unfolded
    ON stripe_cancellation_events (event_created) WHERE folded_at IS NULL;

-- One row per cancellation episode of a subscription. At most one episode per
-- subscription is open (anything but withdrawn): later events update it instead
-- of starting a second investigation.
--   pending    waiting for the settle window, then investigated
--   processing claimed by the worker (lease_expires_at)
--   reported   report written to the outbox
--   withdrawn  the customer resumed before a report went out (no report)
--   excluded   not a customer cancellation (account migration transfer, ...)
--   merged     same customer and episode already reported from another subscription
--   baseline   already scheduled when this feature shipped, never reported
CREATE TABLE IF NOT EXISTS cancellation_reviews (
    id VARCHAR(36) PRIMARY KEY,
    account_id VARCHAR(255) NOT NULL,
    livemode BOOLEAN NOT NULL,
    subscription_id VARCHAR(255) NOT NULL,
    customer_id VARCHAR(255),
    user_id VARCHAR(36),
    kind VARCHAR(20) NOT NULL CHECK (kind IN ('scheduled', 'immediate')),
    status VARCHAR(20) NOT NULL CHECK (
        status IN ('pending', 'processing', 'reported', 'withdrawn', 'excluded', 'merged', 'baseline')
    ),
    origin VARCHAR(20) NOT NULL DEFAULT 'webhook' CHECK (origin IN ('webhook', 'reconciliation', 'baseline')),
    exclusion_reason VARCHAR(80),
    snapshot JSONB NOT NULL DEFAULT '{}',
    snapshot_at TIMESTAMPTZ,
    reported_feedback JSONB,
    requested_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    withdrawn_at TIMESTAMPTZ,
    not_before TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_expires_at TIMESTAMPTZ,
    last_error TEXT,
    evidence JSONB,
    report_text TEXT,
    reported_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cancellation_reviews_open_subscription
    ON cancellation_reviews (subscription_id) WHERE status <> 'withdrawn';
CREATE INDEX IF NOT EXISTS idx_cancellation_reviews_due
    ON cancellation_reviews (not_before) WHERE status IN ('pending', 'processing');
CREATE INDEX IF NOT EXISTS idx_cancellation_reviews_user ON cancellation_reviews (user_id, created_at);

-- Outbox of chat messages (a review's report, or a short update to one). The id is
-- the delivery id the chat side deduplicates on, so a retry never posts twice.
CREATE TABLE IF NOT EXISTS cancellation_report_deliveries (
    id VARCHAR(36) PRIMARY KEY,
    review_id VARCHAR(36) NOT NULL REFERENCES cancellation_reviews(id) ON DELETE CASCADE,
    kind VARCHAR(20) NOT NULL CHECK (kind IN ('report', 'update')),
    message TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cancellation_report_deliveries_due
    ON cancellation_report_deliveries (next_attempt_at) WHERE status = 'pending';

-- Notes from the in-app "Why are you cancelling?" dialog (POST /feedback with
-- category "cancellation"). Until now they only went to Discord. review_id is set
-- once a review has used the note, so a late note becomes an update to its report.
CREATE TABLE IF NOT EXISTS cancellation_notes (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    message TEXT NOT NULL,
    review_id VARCHAR(36) REFERENCES cancellation_reviews(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cancellation_notes_user ON cancellation_notes (user_id, created_at);

-- Cancellations already scheduled before this shipped stay unreported: they get a
-- baseline row, so later events for them update it instead of opening a review.
-- (The account id is left empty: subscription ids are unique across Stripe accounts.)
INSERT INTO cancellation_reviews (
    id, account_id, livemode, subscription_id, customer_id, user_id, kind, status, origin, requested_at
)
SELECT uuid_generate_v4()::text, '', TRUE, u.stripe_subscription_id,
       u.stripe_customer_id, u.id, 'scheduled', 'baseline', 'baseline', u.subscription_cancel_at
FROM users u
WHERE u.subscription_provider = 'stripe'
  AND u.subscription_cancel_at IS NOT NULL
  AND u.stripe_subscription_id IS NOT NULL
ON CONFLICT DO NOTHING
