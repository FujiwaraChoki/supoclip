-- Add task-level transcript storage for transcript editing
ALTER TABLE "tasks"
ADD COLUMN IF NOT EXISTS "transcript_text" TEXT,
ADD COLUMN IF NOT EXISTS "transcript_updated_at" TIMESTAMPTZ;
