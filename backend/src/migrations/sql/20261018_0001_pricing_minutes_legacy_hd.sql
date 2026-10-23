ALTER TABLE users ADD COLUMN IF NOT EXISTS legacy_hd BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS source_duration_seconds INTEGER;
-- Pro moves to 720p for new subscribers, and everyone subscribed today keeps 1080p.
UPDATE users SET legacy_hd = true
WHERE plan = 'pro' AND subscription_status IN ('active', 'trialing');
