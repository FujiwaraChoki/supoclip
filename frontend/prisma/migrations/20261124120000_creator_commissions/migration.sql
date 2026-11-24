-- Creator commissions: who referred each subscription, a ledger of what creators earn
-- per paid invoice (and lose on refunds/disputes), and the Stripe Connect transfers
-- that pay them. Same DDL as backend/src/migrations/sql/20261124_0001_creator_commissions.sql.

ALTER TABLE "affiliates" ADD COLUMN IF NOT EXISTS "stripe_connect_account_id" VARCHAR(255);
ALTER TABLE "affiliates" ADD COLUMN IF NOT EXISTS "stripe_connect_platform_id" VARCHAR(255);
ALTER TABLE "affiliates" ADD COLUMN IF NOT EXISTS "payout_country" VARCHAR(2);
ALTER TABLE "affiliates" ADD COLUMN IF NOT EXISTS "payouts_enabled" BOOLEAN NOT NULL DEFAULT false;
-- Where the creator lives: commission is only earned where Stripe can pay it ("ZZ" = elsewhere).
ALTER TABLE "affiliates" ADD COLUMN IF NOT EXISTS "country" VARCHAR(2);

CREATE TABLE IF NOT EXISTS "affiliate_referrals" (
    "id" VARCHAR(36) PRIMARY KEY,
    "affiliate_user_id" VARCHAR(36) NOT NULL,
    "customer_user_id" VARCHAR(36),
    "stripe_account_id" VARCHAR(255) NOT NULL,
    "stripe_subscription_id" VARCHAR(255) NOT NULL,
    "code" VARCHAR(20) NOT NULL,
    "first_paid_at" TIMESTAMPTZ NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "affiliate_referrals_affiliate_user_id_fkey"
        FOREIGN KEY ("affiliate_user_id") REFERENCES "users"("id")
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "affiliate_referrals_customer_user_id_fkey"
        FOREIGN KEY ("customer_user_id") REFERENCES "users"("id")
        ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "affiliate_referrals_stripe_subscription_id_key"
    ON "affiliate_referrals"("stripe_subscription_id");
CREATE INDEX IF NOT EXISTS "affiliate_referrals_affiliate_user_id_idx"
    ON "affiliate_referrals"("affiliate_user_id");

CREATE TABLE IF NOT EXISTS "affiliate_payouts" (
    "id" VARCHAR(36) PRIMARY KEY,
    "affiliate_user_id" VARCHAR(36) NOT NULL,
    "currency" VARCHAR(3) NOT NULL,
    "amount" INTEGER NOT NULL CHECK ("amount" > 0),
    "stripe_platform_id" VARCHAR(255) NOT NULL,
    "stripe_connect_account_id" VARCHAR(255) NOT NULL,
    "stripe_transfer_id" VARCHAR(255),
    "status" VARCHAR(20) NOT NULL DEFAULT 'pending',
    "failure_reason" VARCHAR(500),
    "created_by" VARCHAR(36),
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "affiliate_payouts_affiliate_user_id_fkey"
        FOREIGN KEY ("affiliate_user_id") REFERENCES "users"("id")
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "affiliate_payouts_created_by_fkey"
        FOREIGN KEY ("created_by") REFERENCES "users"("id")
        ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE INDEX IF NOT EXISTS "affiliate_payouts_affiliate_user_id_created_at_idx"
    ON "affiliate_payouts"("affiliate_user_id", "created_at");
CREATE INDEX IF NOT EXISTS "affiliate_payouts_status_idx" ON "affiliate_payouts"("status");

CREATE TABLE IF NOT EXISTS "affiliate_commissions" (
    "id" VARCHAR(36) PRIMARY KEY,
    "idempotency_key" VARCHAR(255) NOT NULL,
    "affiliate_user_id" VARCHAR(36) NOT NULL,
    "referral_id" VARCHAR(36) NOT NULL,
    "kind" VARCHAR(20) NOT NULL,
    "stripe_account_id" VARCHAR(255) NOT NULL,
    "stripe_invoice_id" VARCHAR(255),
    "stripe_charge_id" VARCHAR(255),
    "currency" VARCHAR(3) NOT NULL,
    "base_amount" INTEGER NOT NULL,
    "amount" INTEGER NOT NULL,
    "rate_bps" INTEGER NOT NULL,
    "occurred_at" TIMESTAMPTZ NOT NULL,
    "available_at" TIMESTAMPTZ NOT NULL,
    "payout_id" VARCHAR(36),
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "affiliate_commissions_affiliate_user_id_fkey"
        FOREIGN KEY ("affiliate_user_id") REFERENCES "users"("id")
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "affiliate_commissions_referral_id_fkey"
        FOREIGN KEY ("referral_id") REFERENCES "affiliate_referrals"("id")
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "affiliate_commissions_payout_id_fkey"
        FOREIGN KEY ("payout_id") REFERENCES "affiliate_payouts"("id")
        ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "affiliate_commissions_idempotency_key_key"
    ON "affiliate_commissions"("idempotency_key");
CREATE INDEX IF NOT EXISTS "affiliate_commissions_affiliate_user_id_payout_id_idx"
    ON "affiliate_commissions"("affiliate_user_id", "payout_id");
CREATE INDEX IF NOT EXISTS "affiliate_commissions_stripe_charge_id_idx"
    ON "affiliate_commissions"("stripe_charge_id");
