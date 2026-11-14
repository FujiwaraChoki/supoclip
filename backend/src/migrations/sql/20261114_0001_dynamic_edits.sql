-- Dynamic edits: the user's edit style per task, a small FX summary per clip, and the rollout flag.
-- Both columns are nullable with no default: NULL means a legacy task or clip, so existing rows and the legacy
-- INSERTs are unchanged. The flag row has no updated_by (a FK to users): init_db runs every migration in one
-- transaction, on an empty users table too. Off on web and iOS, with the admin bypass.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS edit_style JSONB;
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS fx_meta JSONB;
INSERT INTO feature_flags (key, description, web_enabled, ios_enabled, admins_bypass) VALUES ('dynamic_edits', 'Edit intensity, SFX, music and animated caption styles', false, false, true) ON CONFLICT (key) DO NOTHING
