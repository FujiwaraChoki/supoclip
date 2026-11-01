-- Per-clip rescue edits and SEO packaging.
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS clip_package JSONB;
