CREATE TABLE IF NOT EXISTS "affiliates" (
    "id" VARCHAR(36) PRIMARY KEY,
    "user_id" VARCHAR(36) NOT NULL,
    "slug" VARCHAR(20),
    "status" VARCHAR(20) NOT NULL DEFAULT 'pending',
    "platform" VARCHAR(20) NOT NULL,
    "profile_url" VARCHAR(500) NOT NULL,
    "audience_size" VARCHAR(20) NOT NULL,
    "video_url" VARCHAR(500),
    "promotion_plan" VARCHAR(1000),
    "decline_reason" VARCHAR(1000),
    "stripe_promotion_code_id" VARCHAR(255),
    "terms_accepted_at" TIMESTAMPTZ NOT NULL,
    "reviewed_at" TIMESTAMPTZ,
    "reviewed_by" VARCHAR(36),
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "affiliates_user_id_fkey"
        FOREIGN KEY ("user_id") REFERENCES "users"("id")
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "affiliates_reviewed_by_fkey"
        FOREIGN KEY ("reviewed_by") REFERENCES "users"("id")
        ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "affiliates_user_id_key" ON "affiliates"("user_id");
CREATE UNIQUE INDEX IF NOT EXISTS "affiliates_slug_key" ON "affiliates"("slug");
CREATE INDEX IF NOT EXISTS "affiliates_status_created_at_idx" ON "affiliates"("status", "created_at");
