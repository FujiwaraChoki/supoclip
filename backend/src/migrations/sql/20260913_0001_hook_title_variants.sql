-- Stores AI-generated hook title candidates for A/B comparison, plus which
-- one the user picked. hook_title stays the "active"/rendered title;
-- hook_title_variants is a JSON-encoded array of {id, text} candidates
-- (stored as TEXT like analysis_json/stage_timings_json elsewhere in this schema).
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS hook_title_variants TEXT;
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS selected_hook_variant_id VARCHAR(64);
