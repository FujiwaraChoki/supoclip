-- Preview renders: generation encodes a cheap preview of each clip and keeps the
-- inputs of its final pass in the bucket; the full-quality clip is encoded on
-- demand (download, export, publish, studio) and replaces the preview.
-- Rows that exist today are full quality and never change.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS preview_renders BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE generated_clips
    ADD COLUMN IF NOT EXISTS render_quality VARCHAR(10) NOT NULL DEFAULT 'full',
    ADD COLUMN IF NOT EXISTS render_inputs_path TEXT,
    ADD COLUMN IF NOT EXISTS full_render_status VARCHAR(16),
    ADD COLUMN IF NOT EXISTS full_render_progress INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS full_render_error TEXT,
    ADD COLUMN IF NOT EXISTS full_render_updated_at TIMESTAMP WITH TIME ZONE;

-- The stale-render sweep and the task page read the clips still being rendered.
CREATE INDEX IF NOT EXISTS idx_generated_clips_full_render_status
    ON generated_clips (full_render_status) WHERE full_render_status IS NOT NULL;

-- Off everywhere until the rollout; admins see it first.
INSERT INTO feature_flags (key, description, web_enabled, ios_enabled, admins_bypass)
VALUES (
    'preview_renders',
    'Generation renders cheap preview clips; the full-quality clip is rendered when the user downloads, exports, publishes or opens the studio.',
    false,
    false,
    true
)
ON CONFLICT (key) DO NOTHING;
