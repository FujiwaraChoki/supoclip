-- Add admin flag to users
ALTER TABLE "users"
ADD COLUMN IF NOT EXISTS "is_admin" BOOLEAN NOT NULL DEFAULT false;
