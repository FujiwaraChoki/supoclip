-- Reusable, named settings bundles ("templates") a user can save from a
-- project's current settings and apply (replace or merge) onto any other
-- project. `settings` is JSON-encoded text (same pattern as
-- generated_clips.reactions/hook_title_variants) shaped like the
-- task_source:{id} Redis metadata blob: font_family, font_size, font_color,
-- caption_template, include_broll, cleanup_settings, hook_style,
-- social_overlay, broll_settings, output_format, add_subtitles,
-- target_duration_seconds, max_clips. `schema_version` lets future changes to
-- that shape migrate old rows instead of silently misreading them.
CREATE TABLE IF NOT EXISTS project_templates (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    name VARCHAR(200) NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    settings TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_project_templates_user_id ON project_templates(user_id);
