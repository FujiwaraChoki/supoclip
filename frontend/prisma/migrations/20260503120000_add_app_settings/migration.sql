CREATE TABLE IF NOT EXISTS "app_settings" (
    "setting_key" VARCHAR(100) PRIMARY KEY,
    "encrypted_value" TEXT NOT NULL,
    "prefer_admin_value" BOOLEAN NOT NULL DEFAULT false,
    "updated_by" VARCHAR(36),
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "app_settings_updated_by_fkey"
        FOREIGN KEY ("updated_by") REFERENCES "users"("id")
        ON DELETE SET NULL ON UPDATE CASCADE
);

ALTER TABLE "app_settings"
ADD COLUMN IF NOT EXISTS "prefer_admin_value" BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS "app_settings_updated_by_idx" ON "app_settings"("updated_by");
