-- Feature flags, switched per platform from /admin without a deploy.
-- Read by backend/src/feature_flags.py, which caches them for a few seconds.
-- admins_bypass keeps a flag on for admins on every platform while it is off for everyone else.
CREATE TABLE IF NOT EXISTS feature_flags (
    key VARCHAR(64) PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    web_enabled BOOLEAN NOT NULL DEFAULT false,
    ios_enabled BOOLEAN NOT NULL DEFAULT false,
    admins_bypass BOOLEAN NOT NULL DEFAULT true,
    updated_by VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_feature_flags_updated_by ON feature_flags (updated_by);
-- Workflows stay off on web and iOS until the iOS build that has them is approved.
INSERT INTO feature_flags (key, description, web_enabled, ios_enabled, admins_bypass)
VALUES (
    'workflows',
    'Series, topic, recap and trailer, plus instructions and Advanced settings. The WORKFLOWS_ACCESS env gate must allow them too.',
    false,
    false,
    true
)
ON CONFLICT (key) DO NOTHING;
