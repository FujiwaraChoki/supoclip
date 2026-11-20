-- A post the platform refused because SupoClip's shared daily allowance ran out
-- (YouTube's "Video Uploads per day") waits as a scheduled post for the reset.
-- hold_reason tells clients why it is scheduled; held_since bounds how long it
-- may keep waiting and orders held posts oldest first.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS hold_reason VARCHAR(40);
ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS held_since TIMESTAMP WITH TIME ZONE;
