ALTER TABLE "users"
ADD COLUMN IF NOT EXISTS "subscription_provider" VARCHAR(20);

CREATE TABLE IF NOT EXISTS "revenuecat_webhook_events" (
  "id" TEXT PRIMARY KEY,
  "type" TEXT NOT NULL,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
