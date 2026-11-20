ALTER TABLE "users"
ADD COLUMN IF NOT EXISTS "notify_on_publish" BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE "social_posts"
ADD COLUMN IF NOT EXISTS "publish_notification_sent_at" TIMESTAMPTZ;
