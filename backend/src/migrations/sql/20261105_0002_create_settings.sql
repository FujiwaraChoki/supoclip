ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "create_settings" JSONB NOT NULL DEFAULT '{}'::jsonb;
