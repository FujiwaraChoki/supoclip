-- Stripe account routing: which connected Stripe account a user's customer and
-- subscription belong to, plus the guard columns used when claiming webhook
-- events. Mirrors frontend/prisma/migrations/20261106150000_stripe_account_routing
-- so the backend SQL runner creates the same schema Prisma expects. Without
-- users.stripe_account_id, every Prisma user lookup fails (P2022) and sign-up
-- returns a server error.
--
-- Idempotent so it can repair databases that drifted before this migration
-- existed in the runner.
ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_account_id VARCHAR(255);

ALTER TABLE stripe_webhook_events
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'processed',
    ADD COLUMN IF NOT EXISTS claim_token VARCHAR(255),
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS stripe_account_migrations (
    id VARCHAR(255) PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE,
    source_account VARCHAR(255) NOT NULL,
    source_customer VARCHAR(255) NOT NULL,
    source_subscription VARCHAR(255) NOT NULL,
    destination_account VARCHAR(255) NOT NULL,
    destination_customer VARCHAR(255) NOT NULL,
    destination_subscription VARCHAR(255),
    destination_schedule VARCHAR(255),
    stage VARCHAR(32) NOT NULL CHECK (stage IN ('prepared','source_stopped','destination_staged','destination_active','rollback_verified','cancelled')),
    revision INTEGER NOT NULL DEFAULT 0,
    paid_through TIMESTAMPTZ NOT NULL,
    source_stopped_verified BOOLEAN NOT NULL DEFAULT false,
    source_resumed_verified BOOLEAN NOT NULL DEFAULT false,
    destination_disabled_verified BOOLEAN NOT NULL DEFAULT false,
    destination_paid_invoice VARCHAR(255),
    cancellation_requested BOOLEAN NOT NULL DEFAULT false,
    recovery_required BOOLEAN NOT NULL DEFAULT false,
    review_reason TEXT,
    livemode BOOLEAN NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_account, source_customer),
    UNIQUE (destination_account, destination_customer),
    CHECK (source_account <> destination_account)
);