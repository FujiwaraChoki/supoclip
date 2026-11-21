-- Instagram Reels publishing stays off on web and iOS until Meta App Review passes.
-- Admins bypass the flag so test Reels can be published in production before rollout.
INSERT INTO feature_flags (key, description, web_enabled, ios_enabled, admins_bypass)
VALUES (
    'instagram_publishing',
    'Instagram Reels connecting and publishing. Off hides Instagram as coming soon while letting admins test.',
    false,
    false,
    true
)
ON CONFLICT (key) DO NOTHING;
