-- Mirrors backend/src/migrations/sql/20261124_0002_pebble_onboarding.sql (columns only).
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "onboarding_variant" VARCHAR(16);
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "onboarding_answers" JSONB;
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "onboarding_completed_at" TIMESTAMPTZ;
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "preview_used_at" TIMESTAMPTZ;
ALTER TABLE "tasks" ADD COLUMN IF NOT EXISTS "preview" JSONB;
