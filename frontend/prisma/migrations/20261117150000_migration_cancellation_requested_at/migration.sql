-- When the customer cancelled in the app during an account migration. Cancellation
-- reviews report this time instead of the migration's own source stop. Rows that
-- were cancelled before this column existed stay NULL; reviews say so.
ALTER TABLE "stripe_account_migrations" ADD COLUMN IF NOT EXISTS "cancellation_requested_at" TIMESTAMPTZ;
