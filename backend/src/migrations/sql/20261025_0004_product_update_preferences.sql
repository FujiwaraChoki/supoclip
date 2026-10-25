-- Separate product announcements from account, billing and clip-ready emails.
ALTER TABLE users ADD COLUMN IF NOT EXISTS product_updates_unsubscribed_at TIMESTAMP WITH TIME ZONE;
