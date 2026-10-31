-- Virality rubric detail (5 subscores, penalties, hook quote, why it works, editor fix).
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS virality_breakdown JSONB;
