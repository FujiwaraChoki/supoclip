CREATE TABLE IF NOT EXISTS "stripe_webhook_events" (
  "id" TEXT PRIMARY KEY,
  "type" TEXT NOT NULL,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
