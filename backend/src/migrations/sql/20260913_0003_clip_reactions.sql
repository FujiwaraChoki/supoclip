-- Emoji reaction overlays burned into a clip alongside its hook title/captions.
-- JSON-encoded array as text, same pattern as hook_title_variants:
-- [{"id", "emoji", "timestamp_seconds", "animation_style", "duration_seconds",
--   "position": {"x_pct", "y_pct"}}]
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS reactions TEXT DEFAULT '[]';
