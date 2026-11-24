-- Pebble onboarding and the free preview clip (docs/onboarding.md).
--
-- users.onboarding_variant: 'pebble' or 'classic', assigned once to accounts created after the
--   launch (the A/B split); NULL for older accounts, which never see either onboarding.
-- users.onboarding_answers: what the person said they make, for examples and plan advice.
-- users.onboarding_completed_at: when the guided onboarding ended (finished or skipped).
-- users.preview_used_at: when the account's one free preview clip finished.
-- tasks.preview: set on a free preview project. {"status": "pending"} at creation; when it
--   finishes, the best clip is rendered and the rest are listed as locked moments; "unlocked_at"
--   once a plan renders the rest in place. NULL on every other project.
-- Undo: ALTER TABLE users DROP COLUMN onboarding_variant, DROP COLUMN onboarding_answers,
--       DROP COLUMN onboarding_completed_at, DROP COLUMN preview_used_at;
--       ALTER TABLE tasks DROP COLUMN preview; DELETE FROM feature_flags WHERE key = 'pebble_onboarding';
ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_variant VARCHAR(16);
ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_answers JSONB;
ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_completed_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS preview_used_at TIMESTAMPTZ;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS preview JSONB;
-- The daily cap counts today's previews across everyone.
CREATE INDEX IF NOT EXISTS ix_tasks_preview_created ON tasks (created_at) WHERE preview IS NOT NULL;

-- On for web, off for iOS (its own onboarding). Off: nobody is put in the Pebble group and no
-- free previews start; accounts already in it get the classic onboarding.
INSERT INTO feature_flags (key, description, web_enabled, ios_enabled, admins_bypass)
VALUES (
    'pebble_onboarding',
    'New accounts: the onboarding guided by Agent, with one free preview clip from their own video (A/B against the classic onboarding).',
    true,
    false,
    true
)
ON CONFLICT (key) DO NOTHING;
