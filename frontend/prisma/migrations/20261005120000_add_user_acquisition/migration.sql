CREATE TABLE IF NOT EXISTS "user_acquisition" (
    "user_id" VARCHAR(36) PRIMARY KEY,
    "utm_source" VARCHAR(100),
    "utm_medium" VARCHAR(100),
    "utm_campaign" VARCHAR(100),
    "utm_content" VARCHAR(100),
    "utm_term" VARCHAR(100),
    "ref" VARCHAR(100),
    "referrer_host" VARCHAR(255),
    "landing_path" VARCHAR(512),
    "first_seen_at" TIMESTAMPTZ,
    "first_clip_exported_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "user_acquisition_user_id_fkey"
        FOREIGN KEY ("user_id") REFERENCES "users"("id")
        ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE INDEX IF NOT EXISTS "user_acquisition_utm_source_utm_campaign_idx"
    ON "user_acquisition"("utm_source", "utm_campaign");
