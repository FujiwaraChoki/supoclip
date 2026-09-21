-- Ranking tool phase 2: persistent per-folder clip library, for random +
-- prefer-unused selection and cross-ranking text memory (see
-- CLAUDE.md's Ranking tool section). A "folder" is a user-named batch of
-- clips picked together in the browser (directory picker or multi-file
-- drop) — there is no server filesystem path to scan, uploads are the only
-- way a clip reaches the backend.
CREATE TABLE IF NOT EXISTS ranking_folders (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS ranking_folder_clips (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    folder_id VARCHAR(36) NOT NULL REFERENCES ranking_folders(id) ON DELETE CASCADE,
    file_path VARCHAR(500) NOT NULL,
    original_filename VARCHAR(500) NOT NULL,
    duration_seconds FLOAT,
    thumbnail_path VARCHAR(500) NULL,
    content_hash VARCHAR(64) NOT NULL,
    saved_text TEXT NULL,
    use_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TIMESTAMP WITH TIME ZONE NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (folder_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_ranking_folder_clips_folder_id ON ranking_folder_clips(folder_id, use_count);

ALTER TABLE ranking_inputs ADD COLUMN IF NOT EXISTS folder_clip_id VARCHAR(36) NULL;
ALTER TABLE ranking_inputs ADD COLUMN IF NOT EXISTS rank_text TEXT NULL;
ALTER TABLE ranking_inputs ADD COLUMN IF NOT EXISTS framing VARCHAR(20) NOT NULL DEFAULT 'blur_fill' CHECK (framing IN ('blur_fill', 'crop_fill', 'letterbox'));
