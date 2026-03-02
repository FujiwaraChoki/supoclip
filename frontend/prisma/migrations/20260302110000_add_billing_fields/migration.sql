-- Add monetization/billing fields to users
ALTER TABLE "users"
ADD COLUMN IF NOT EXISTS "plan" VARCHAR(20) NOT NULL DEFAULT 'free',
ADD COLUMN IF NOT EXISTS "subscription_status" VARCHAR(20) NOT NULL DEFAULT 'inactive',
ADD COLUMN IF NOT EXISTS "stripe_customer_id" VARCHAR(255),
ADD COLUMN IF NOT EXISTS "stripe_subscription_id" VARCHAR(255),
ADD COLUMN IF NOT EXISTS "billing_period_start" TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS "billing_period_end" TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS "trial_ends_at" TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS "users_stripe_customer_id_key" ON "users"("stripe_customer_id");
CREATE UNIQUE INDEX IF NOT EXISTS "users_stripe_subscription_id_key" ON "users"("stripe_subscription_id");
