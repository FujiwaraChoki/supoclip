-- Per-clip SEO metadata (title/description/tags), generated one LLM call per
-- video (not per clip) and cached here so it's never regenerated on render,
-- preview, or re-open. `_user_edited` flags let a passive/automatic
-- regeneration (transcript change, re-cut) skip fields the user manually
-- edited, while the explicit "Regenerate" button always overwrites
-- everything and resets these flags — see MetadataService.
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS metadata_title VARCHAR(80);
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS metadata_description VARCHAR(200);
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS metadata_tags TEXT;
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS metadata_title_user_edited BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS metadata_description_user_edited BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS metadata_tags_user_edited BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS metadata_provider VARCHAR(10);
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS metadata_generated_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS metadata_generation_ms INTEGER;

-- Free-form theme the AI is asked to keep consistent across a project's
-- clips (e.g. "football", "sleep") — one value per project, not per clip.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS metadata_project_theme VARCHAR(60);
