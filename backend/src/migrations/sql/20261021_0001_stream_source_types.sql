-- Allow persisted Twitch and Kick sources. Older installations used either
-- SQLAlchemy's explicit name or PostgreSQL's generated column-check name.
ALTER TABLE sources DROP CONSTRAINT IF EXISTS check_source_type;
ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_type_check;
ALTER TABLE sources ADD CONSTRAINT check_source_type
    CHECK (type IN ('youtube', 'video_url', 'twitch', 'kick'));
