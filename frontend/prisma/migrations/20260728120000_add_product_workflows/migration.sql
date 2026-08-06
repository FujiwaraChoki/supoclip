-- Shared workflow schema is also applied by the backend migration runner.
-- Keep this migration idempotent so Prisma-based deployments remain compatible.
ALTER TABLE "tasks" ADD COLUMN IF NOT EXISTS "workspace_id" VARCHAR(36);
ALTER TABLE "tasks" ADD COLUMN IF NOT EXISTS "brand_kit_id" VARCHAR(36);
ALTER TABLE "tasks" ADD COLUMN IF NOT EXISTS "generation_preferences_json" TEXT;
ALTER TABLE "tasks" ADD COLUMN IF NOT EXISTS "target_language" VARCHAR(32);
ALTER TABLE "tasks" ADD COLUMN IF NOT EXISTS "analysis_mode" VARCHAR(24) NOT NULL DEFAULT 'transcript';
ALTER TABLE "sources" DROP CONSTRAINT IF EXISTS "check_source_type";
ALTER TABLE "sources" ADD CONSTRAINT "check_source_type" CHECK ("type" IN ('youtube', 'video_url', 'external'));

CREATE TABLE IF NOT EXISTS "workspaces" (
  "id" VARCHAR(36) PRIMARY KEY,
  "owner_id" VARCHAR(36) NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "name" VARCHAR(160) NOT NULL,
  "slug" VARCHAR(180) NOT NULL UNIQUE,
  "logo_url" TEXT,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS "workspace_members" (
  "id" VARCHAR(36) PRIMARY KEY,
  "workspace_id" VARCHAR(36) NOT NULL REFERENCES "workspaces"("id") ON DELETE CASCADE,
  "user_id" VARCHAR(36) REFERENCES "users"("id") ON DELETE CASCADE,
  "email" VARCHAR(255) NOT NULL,
  "role" VARCHAR(24) NOT NULL DEFAULT 'member',
  "status" VARCHAR(24) NOT NULL DEFAULT 'invited',
  "invite_token" VARCHAR(64) UNIQUE,
  "invited_by" VARCHAR(36) REFERENCES "users"("id") ON DELETE SET NULL,
  "joined_at" TIMESTAMPTZ,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE ("workspace_id", "email")
);
CREATE TABLE IF NOT EXISTS "brand_kits" (
  "id" VARCHAR(36) PRIMARY KEY,
  "workspace_id" VARCHAR(36) REFERENCES "workspaces"("id") ON DELETE CASCADE,
  "user_id" VARCHAR(36) NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "name" VARCHAR(160) NOT NULL,
  "is_default" BOOLEAN NOT NULL DEFAULT false,
  "settings_json" TEXT NOT NULL DEFAULT '{}',
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS "media_assets" (
  "id" VARCHAR(36) PRIMARY KEY,
  "workspace_id" VARCHAR(36) REFERENCES "workspaces"("id") ON DELETE CASCADE,
  "user_id" VARCHAR(36) NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "brand_kit_id" VARCHAR(36) REFERENCES "brand_kits"("id") ON DELETE SET NULL,
  "asset_type" VARCHAR(32) NOT NULL,
  "name" VARCHAR(255) NOT NULL,
  "file_path" TEXT NOT NULL,
  "mime_type" VARCHAR(160),
  "size_bytes" BIGINT NOT NULL DEFAULT 0,
  "metadata_json" TEXT NOT NULL DEFAULT '{}',
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS "social_accounts" (
  "id" VARCHAR(36) PRIMARY KEY,
  "workspace_id" VARCHAR(36) REFERENCES "workspaces"("id") ON DELETE CASCADE,
  "user_id" VARCHAR(36) NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "platform" VARCHAR(32) NOT NULL,
  "external_account_id" VARCHAR(255) NOT NULL,
  "display_name" VARCHAR(255) NOT NULL,
  "access_token_encrypted" TEXT NOT NULL,
  "refresh_token_encrypted" TEXT,
  "token_expires_at" TIMESTAMPTZ,
  "scopes" TEXT,
  "status" VARCHAR(24) NOT NULL DEFAULT 'active',
  "metadata_json" TEXT NOT NULL DEFAULT '{}',
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE ("workspace_id", "platform", "external_account_id")
);
CREATE TABLE IF NOT EXISTS "social_posts" (
  "id" VARCHAR(36) PRIMARY KEY,
  "workspace_id" VARCHAR(36) REFERENCES "workspaces"("id") ON DELETE CASCADE,
  "user_id" VARCHAR(36) NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "task_id" VARCHAR(36) REFERENCES "tasks"("id") ON DELETE CASCADE,
  "clip_id" VARCHAR(36) REFERENCES "generated_clips"("id") ON DELETE CASCADE,
  "social_account_id" VARCHAR(36) NOT NULL REFERENCES "social_accounts"("id") ON DELETE CASCADE,
  "platform" VARCHAR(32) NOT NULL,
  "title" TEXT,
  "description" TEXT,
  "hashtags" TEXT,
  "thumbnail_path" TEXT,
  "scheduled_for" TIMESTAMPTZ,
  "published_at" TIMESTAMPTZ,
  "status" VARCHAR(24) NOT NULL DEFAULT 'draft',
  "external_post_id" VARCHAR(255),
  "attempt_count" INTEGER NOT NULL DEFAULT 0,
  "last_error" TEXT,
  "metadata_json" TEXT NOT NULL DEFAULT '{}',
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS "source_subscriptions" (
  "id" VARCHAR(36) PRIMARY KEY,
  "workspace_id" VARCHAR(36) REFERENCES "workspaces"("id") ON DELETE CASCADE,
  "user_id" VARCHAR(36) NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "provider" VARCHAR(32) NOT NULL,
  "external_source_id" VARCHAR(255) NOT NULL,
  "source_url" TEXT NOT NULL,
  "display_name" VARCHAR(255) NOT NULL,
  "enabled" BOOLEAN NOT NULL DEFAULT true,
  "last_seen_item_id" VARCHAR(255),
  "last_checked_at" TIMESTAMPTZ,
  "settings_json" TEXT NOT NULL DEFAULT '{}',
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE ("user_id", "provider", "external_source_id")
);
CREATE TABLE IF NOT EXISTS "collections" (
  "id" VARCHAR(36) PRIMARY KEY,
  "workspace_id" VARCHAR(36) REFERENCES "workspaces"("id") ON DELETE CASCADE,
  "user_id" VARCHAR(36) NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "name" VARCHAR(160) NOT NULL,
  "description" TEXT,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS "collection_clips" (
  "collection_id" VARCHAR(36) NOT NULL REFERENCES "collections"("id") ON DELETE CASCADE,
  "clip_id" VARCHAR(36) NOT NULL REFERENCES "generated_clips"("id") ON DELETE CASCADE,
  "added_by" VARCHAR(36) REFERENCES "users"("id") ON DELETE SET NULL,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("collection_id", "clip_id")
);
CREATE TABLE IF NOT EXISTS "clip_variants" (
  "id" VARCHAR(36) PRIMARY KEY,
  "clip_id" VARCHAR(36) NOT NULL REFERENCES "generated_clips"("id") ON DELETE CASCADE,
  "user_id" VARCHAR(36) NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "variant_type" VARCHAR(32) NOT NULL,
  "language" VARCHAR(32),
  "voice" VARCHAR(120),
  "status" VARCHAR(24) NOT NULL DEFAULT 'queued',
  "file_path" TEXT,
  "transcript_text" TEXT,
  "metadata_json" TEXT NOT NULL DEFAULT '{}',
  "error" TEXT,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS "clip_broll_items" (
  "id" VARCHAR(36) PRIMARY KEY,
  "clip_id" VARCHAR(36) NOT NULL REFERENCES "generated_clips"("id") ON DELETE CASCADE,
  "user_id" VARCHAR(36) NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "asset_id" VARCHAR(36) REFERENCES "media_assets"("id") ON DELETE SET NULL,
  "provider" VARCHAR(32) NOT NULL DEFAULT 'pexels',
  "media_type" VARCHAR(24) NOT NULL DEFAULT 'stock_video',
  "prompt" TEXT,
  "source_url" TEXT,
  "file_path" TEXT,
  "start_seconds" DOUBLE PRECISION NOT NULL DEFAULT 0,
  "end_seconds" DOUBLE PRECISION NOT NULL DEFAULT 0,
  "crop_json" TEXT NOT NULL DEFAULT '{}',
  "sort_order" INTEGER NOT NULL DEFAULT 0,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS "export_jobs" (
  "id" VARCHAR(36) PRIMARY KEY,
  "workspace_id" VARCHAR(36) REFERENCES "workspaces"("id") ON DELETE CASCADE,
  "user_id" VARCHAR(36) NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "task_id" VARCHAR(36) REFERENCES "tasks"("id") ON DELETE CASCADE,
  "collection_id" VARCHAR(36) REFERENCES "collections"("id") ON DELETE CASCADE,
  "export_type" VARCHAR(32) NOT NULL,
  "status" VARCHAR(24) NOT NULL DEFAULT 'queued',
  "file_path" TEXT,
  "settings_json" TEXT NOT NULL DEFAULT '{}',
  "error" TEXT,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS "webhook_endpoints" (
  "id" VARCHAR(36) PRIMARY KEY,
  "workspace_id" VARCHAR(36) REFERENCES "workspaces"("id") ON DELETE CASCADE,
  "user_id" VARCHAR(36) NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "url" TEXT NOT NULL,
  "secret_encrypted" TEXT NOT NULL,
  "events" TEXT NOT NULL,
  "enabled" BOOLEAN NOT NULL DEFAULT true,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS "webhook_deliveries" (
  "id" VARCHAR(36) PRIMARY KEY,
  "endpoint_id" VARCHAR(36) NOT NULL REFERENCES "webhook_endpoints"("id") ON DELETE CASCADE,
  "event_type" VARCHAR(80) NOT NULL,
  "payload_json" TEXT NOT NULL,
  "status" VARCHAR(24) NOT NULL DEFAULT 'queued',
  "response_status" INTEGER,
  "attempt_count" INTEGER NOT NULL DEFAULT 0,
  "next_attempt_at" TIMESTAMPTZ,
  "last_error" TEXT,
  "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS "idx_tasks_workspace_id" ON "tasks"("workspace_id");
CREATE INDEX IF NOT EXISTS "idx_workspace_members_user_id" ON "workspace_members"("user_id");
CREATE INDEX IF NOT EXISTS "idx_brand_kits_user_id" ON "brand_kits"("user_id");
CREATE INDEX IF NOT EXISTS "idx_media_assets_user_id" ON "media_assets"("user_id");
CREATE INDEX IF NOT EXISTS "idx_social_posts_schedule" ON "social_posts"("status", "scheduled_for");
CREATE INDEX IF NOT EXISTS "idx_source_subscriptions_enabled" ON "source_subscriptions"("enabled", "last_checked_at");
CREATE INDEX IF NOT EXISTS "idx_clip_variants_clip_id" ON "clip_variants"("clip_id");
CREATE INDEX IF NOT EXISTS "idx_clip_broll_items_clip_id" ON "clip_broll_items"("clip_id");
CREATE INDEX IF NOT EXISTS "idx_webhook_deliveries_retry" ON "webhook_deliveries"("status", "next_attempt_at");
