-- TikTok publishing is paused until TikTok audits the app's Direct Post access:
-- until then TikTok rejects posts from public accounts. Turn the flag on per
-- platform in /admin once the audit passes. No admin bypass, so admins see what users see.
INSERT INTO feature_flags (key, description, web_enabled, ios_enabled, admins_bypass)
VALUES (
    'tiktok_publishing',
    'TikTok connecting and publishing. Off shows TikTok as paused with a short notice. Turn on once TikTok''s Direct Post audit passes.',
    false,
    false,
    false
)
ON CONFLICT (key) DO NOTHING;
