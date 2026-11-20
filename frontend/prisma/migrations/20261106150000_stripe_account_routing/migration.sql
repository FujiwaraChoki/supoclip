ALTER TABLE "users" ADD COLUMN "stripe_account_id" VARCHAR(255);
ALTER TABLE "stripe_webhook_events"
  ADD COLUMN "status" VARCHAR(20) NOT NULL DEFAULT 'processed',
  ADD COLUMN "claim_token" VARCHAR(255),
  ADD COLUMN "lease_expires_at" TIMESTAMPTZ;

CREATE TABLE "stripe_account_migrations" (
  "id" VARCHAR(255) PRIMARY KEY,
  "user_id" VARCHAR(255) NOT NULL UNIQUE REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE,
  "source_account" VARCHAR(255) NOT NULL,
  "source_customer" VARCHAR(255) NOT NULL,
  "source_subscription" VARCHAR(255) NOT NULL,
  "destination_account" VARCHAR(255) NOT NULL,
  "destination_customer" VARCHAR(255) NOT NULL,
  "destination_subscription" VARCHAR(255),
  "destination_schedule" VARCHAR(255),
  "stage" VARCHAR(32) NOT NULL CHECK ("stage" IN ('prepared','source_stopped','destination_staged','destination_active','rollback_verified','cancelled')),
  "revision" INTEGER NOT NULL DEFAULT 0,
  "paid_through" TIMESTAMPTZ NOT NULL,
  "source_stopped_verified" BOOLEAN NOT NULL DEFAULT false,
  "source_resumed_verified" BOOLEAN NOT NULL DEFAULT false,
  "destination_disabled_verified" BOOLEAN NOT NULL DEFAULT false,
  "destination_paid_invoice" VARCHAR(255),
  "cancellation_requested" BOOLEAN NOT NULL DEFAULT false,
  "recovery_required" BOOLEAN NOT NULL DEFAULT false,
  "review_reason" TEXT,
  "livemode" BOOLEAN NOT NULL,
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE ("source_account", "source_customer"),
  UNIQUE ("destination_account", "destination_customer"),
  CHECK ("source_account" <> "destination_account")
);
