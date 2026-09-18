ALTER TABLE "tasks"
ADD COLUMN IF NOT EXISTS "include_hook_titles" BOOLEAN DEFAULT true;