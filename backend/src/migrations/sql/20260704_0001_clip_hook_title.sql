-- AI-written on-screen hook titles for generated clips.
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS hook_title VARCHAR(200);
