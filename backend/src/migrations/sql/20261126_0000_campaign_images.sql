-- Campaign images: an optional picture a creator attaches to a project (for example a
-- sponsor or reward-campaign banner). Every clip the project renders shows it in the band
-- above the video for the whole clip (media/campaign_image.py).
--
-- The image lives in PostgreSQL, not on disk: a worker may run on another machine.
-- It is normalised to PNG and capped at a few MB on upload, so rows stay small.
-- tasks.source_metadata->>'campaign_image_id' points at the row a project uses.
-- Undo: DROP TABLE campaign_images;
CREATE TABLE IF NOT EXISTS campaign_images (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content_type VARCHAR(32) NOT NULL DEFAULT 'image/png',
    data BYTEA NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    byte_size INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_campaign_images_user ON campaign_images (user_id, created_at DESC);
