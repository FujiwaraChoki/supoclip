-- Database initialization script for SupoClip
-- Create database schema with required tables

-- Enable UUID extension for generating UUIDs
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Users table (compatible with Prisma schema)
CREATE TABLE users (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    "emailVerified" BOOLEAN NOT NULL DEFAULT false,
    image VARCHAR(500),
    "createdAt" TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    password_hash VARCHAR(255),
    -- Default font preferences
    default_font_family VARCHAR(100) DEFAULT 'TikTokSans-Regular',
    default_font_size INTEGER DEFAULT 24,
    default_font_color VARCHAR(7) DEFAULT '#FFFFFF',
    notify_on_completion BOOLEAN NOT NULL DEFAULT true,
    -- Monetization and billing fields
    is_admin BOOLEAN NOT NULL DEFAULT false,
    plan VARCHAR(20) NOT NULL DEFAULT 'free',
    subscription_status VARCHAR(20) NOT NULL DEFAULT 'inactive',
    subscription_provider VARCHAR(20),
    stripe_customer_id VARCHAR(255) UNIQUE,
    stripe_subscription_id VARCHAR(255) UNIQUE,
    billing_period_start TIMESTAMP WITH TIME ZONE,
    billing_period_end TIMESTAMP WITH TIME ZONE,
    trial_ends_at TIMESTAMP WITH TIME ZONE
);

-- Source table (created before tasks since tasks reference sources)
CREATE TABLE sources (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    type VARCHAR(20) CHECK (type IN ('youtube', 'video_url')) NOT NULL,
    title VARCHAR(500) NOT NULL,
    url VARCHAR(1000),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Tasks table
CREATE TABLE tasks (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_id VARCHAR(36) REFERENCES sources(id) ON DELETE SET NULL,
    generated_clips_ids VARCHAR(36)[], -- Array of clip IDs
    status VARCHAR(20) NOT NULL DEFAULT 'pending',

    -- Progress tracking fields
    progress INTEGER DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    progress_message TEXT,

    -- Font customization fields
    font_family VARCHAR(100) DEFAULT 'TikTokSans-Regular',
    font_size INTEGER DEFAULT 24,
    font_color VARCHAR(7) DEFAULT '#FFFFFF', -- Hex color code

    -- Caption template and B-roll options
    caption_template VARCHAR(50) DEFAULT 'default',
    include_broll BOOLEAN DEFAULT false,
    processing_mode VARCHAR(20) NOT NULL DEFAULT 'fast',

    -- Multi-tool platform: 'clipping' (one source -> many clips, the
    -- original pipeline) or 'ranking' (N inputs -> one compilation). See
    -- docs/architecture.md's "Multi-Tool Platform & Ranking Tool" section.
    task_type VARCHAR(20) NOT NULL DEFAULT 'clipping' CHECK (task_type IN ('clipping', 'ranking')),
    ranking_settings TEXT NULL, -- JSON-encoded {template_id, number_overlay, export_preset, target_lufs}
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    cache_hit BOOLEAN NOT NULL DEFAULT false,
    error_code VARCHAR(80),
    stage_timings_json TEXT,
    completion_notification_sent_at TIMESTAMP WITH TIME ZONE,
    share_token VARCHAR(64),
    share_enabled BOOLEAN NOT NULL DEFAULT false,

    -- Soft delete: set on DELETE instead of removing the row, so a task (and
    -- its clips) can be restored from trash. A separate purge path hard-deletes.
    deleted_at TIMESTAMPTZ NULL,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Generated clips table
CREATE TABLE generated_clips (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    task_id VARCHAR(36) NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    start_time VARCHAR(20) NOT NULL, -- MM:SS format
    end_time VARCHAR(20) NOT NULL,   -- MM:SS format
    duration FLOAT NOT NULL,         -- Duration in seconds
    text TEXT,                       -- Transcript text for this clip
    relevance_score FLOAT NOT NULL,
    reasoning TEXT,                  -- AI reasoning for selection
    clip_order INTEGER NOT NULL,     -- Order within the task

    -- Virality score breakdown
    virality_score INTEGER DEFAULT 0,
    hook_score INTEGER DEFAULT 0,
    engagement_score INTEGER DEFAULT 0,
    value_score INTEGER DEFAULT 0,
    shareability_score INTEGER DEFAULT 0,
    hook_type VARCHAR(50),
    hook_title VARCHAR(200),         -- AI-written on-screen headline
    hook_title_variants TEXT,        -- JSON-encoded [{id, text}] alternative hooks for A/B comparison
    selected_hook_variant_id VARCHAR(64), -- which variant (or "custom") is currently applied
    reactions TEXT DEFAULT '[]',     -- JSON-encoded [{id, emoji, timestamp_seconds, animation_style, duration_seconds, position}] burned-in emoji reactions

    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Ranking tool: the N source videos one ranking project holds (the inverse
-- cardinality of clipping's one-source/many-clips model above). The rendered
-- compilation itself reuses generated_clips as a single row (clip_order=0)
-- rather than a parallel output table.
CREATE TABLE ranking_inputs (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    task_id VARCHAR(36) NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    file_path VARCHAR(500) NOT NULL,
    original_filename VARCHAR(500) NOT NULL,
    duration_seconds FLOAT,
    order_index INTEGER NOT NULL,
    rank_position INTEGER NULL,
    thumbnail_path VARCHAR(500) NULL,

    -- Folder-library linkage (added for random/prefer-unused selection and
    -- cross-ranking text memory): which library clip this input came from,
    -- this ranking's own copy of that clip's display text (pre-filled from
    -- ranking_folder_clips.saved_text but independently editable), and a
    -- per-clip framing override for non-9:16 source footage.
    folder_clip_id VARCHAR(36) NULL,
    rank_text TEXT NULL,
    framing VARCHAR(20) NOT NULL DEFAULT 'blur_fill' CHECK (framing IN ('blur_fill', 'crop_fill', 'letterbox')),

    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_ranking_inputs_task_id ON ranking_inputs(task_id, order_index);

-- A "folder" is a user-named batch of clips selected together (via a browser
-- directory picker or a multi-file drop — there is no server filesystem path
-- to scan, uploads are the only way a clip reaches the backend). Clips
-- persist here across ranking projects so the same folder can be drawn from
-- repeatedly with "prefer unused" selection and remembered per-clip text.
CREATE TABLE ranking_folders (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, name)
);

CREATE TABLE ranking_folder_clips (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    folder_id VARCHAR(36) NOT NULL REFERENCES ranking_folders(id) ON DELETE CASCADE,
    file_path VARCHAR(500) NOT NULL,
    original_filename VARCHAR(500) NOT NULL,
    duration_seconds FLOAT,
    thumbnail_path VARCHAR(500) NULL,
    -- Content identity so the same file re-selected/re-dropped later (even
    -- under a different filename) resolves to the same library row instead
    -- of duplicating it — "path + hash" per the text-memory spec.
    content_hash VARCHAR(64) NOT NULL,
    saved_text TEXT NULL,
    use_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TIMESTAMP WITH TIME ZONE NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (folder_id, content_hash)
);

CREATE INDEX idx_ranking_folder_clips_folder_id ON ranking_folder_clips(folder_id, use_count);

CREATE TABLE processing_cache (
    cache_key VARCHAR(255) PRIMARY KEY,
    source_url TEXT NOT NULL,
    source_type VARCHAR(20) NOT NULL,
    video_path TEXT,
    transcript_text TEXT,
    analysis_json TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Better Auth tables
CREATE TABLE session (
    id VARCHAR(36) PRIMARY KEY,
    "expiresAt" TIMESTAMP WITH TIME ZONE NOT NULL,
    token VARCHAR(255) UNIQUE NOT NULL,
    "createdAt" TIMESTAMP WITH TIME ZONE NOT NULL,
    "updatedAt" TIMESTAMP WITH TIME ZONE NOT NULL,
    "ipAddress" VARCHAR(255),
    "userAgent" TEXT,
    "userId" VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE account (
    id VARCHAR(36) PRIMARY KEY,
    "accountId" VARCHAR(255) NOT NULL,
    "providerId" VARCHAR(255) NOT NULL,
    "userId" VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    "accessToken" TEXT,
    "refreshToken" TEXT,
    "idToken" TEXT,
    "accessTokenExpiresAt" TIMESTAMP WITH TIME ZONE,
    "refreshTokenExpiresAt" TIMESTAMP WITH TIME ZONE,
    scope TEXT,
    password TEXT,
    "createdAt" TIMESTAMP WITH TIME ZONE NOT NULL,
    "updatedAt" TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE verification (
    id VARCHAR(36) PRIMARY KEY,
    identifier VARCHAR(255) NOT NULL,
    value VARCHAR(255) NOT NULL,
    "expiresAt" TIMESTAMP WITH TIME ZONE NOT NULL,
    "createdAt" TIMESTAMP WITH TIME ZONE,
    "updatedAt" TIMESTAMP WITH TIME ZONE
);

-- Stripe webhook idempotency table
CREATE TABLE stripe_webhook_events (
    id VARCHAR(255) PRIMARY KEY,
    type VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- RevenueCat webhook idempotency table
CREATE TABLE revenuecat_webhook_events (
    id VARCHAR(255) PRIMARY KEY,
    type VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE app_settings (
    setting_key VARCHAR(100) PRIMARY KEY,
    encrypted_value TEXT NOT NULL,
    prefer_admin_value BOOLEAN NOT NULL DEFAULT false,
    updated_by VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Per-user API keys for programmatic access (e.g. the MCP server)
CREATE TABLE api_keys (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(120) NOT NULL DEFAULT 'API Key',
    key_hash VARCHAR(64) NOT NULL UNIQUE,
    key_prefix VARCHAR(16) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP WITH TIME ZONE,
    revoked_at TIMESTAMP WITH TIME ZONE
);

-- Create indexes for better performance
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_tasks_user_id ON tasks(user_id);
CREATE INDEX idx_tasks_source_id ON tasks(source_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_created_at ON tasks(created_at);
CREATE INDEX idx_tasks_processing_mode ON tasks(processing_mode);
CREATE INDEX idx_tasks_completed_at ON tasks(completed_at);
CREATE INDEX idx_tasks_deleted_at ON tasks(deleted_at);
CREATE UNIQUE INDEX idx_tasks_share_token ON tasks(share_token) WHERE share_token IS NOT NULL;
CREATE INDEX idx_sources_created_at ON sources(created_at);
CREATE INDEX idx_processing_cache_source_url ON processing_cache(source_url);
CREATE INDEX idx_generated_clips_task_id ON generated_clips(task_id);
CREATE INDEX idx_generated_clips_clip_order ON generated_clips(clip_order);
CREATE INDEX idx_generated_clips_created_at ON generated_clips(created_at);
CREATE INDEX idx_session_token ON session(token);
CREATE INDEX idx_session_userId ON session("userId");
CREATE INDEX idx_account_userId ON account("userId");
CREATE INDEX idx_verification_identifier ON verification(identifier);
CREATE INDEX idx_app_settings_updated_by ON app_settings(updated_by);
CREATE INDEX idx_api_keys_user_id ON api_keys(user_id);
CREATE INDEX idx_api_keys_key_hash ON api_keys(key_hash);

-- Create updated_at trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Create function for updatedAt column (Prisma format)
CREATE OR REPLACE FUNCTION update_updatedAt_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW."updatedAt" = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Create triggers for updated_at and updatedAt
-- Users table only has "updatedAt" (Better Auth convention)
CREATE TRIGGER update_users_updatedAt BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION update_updatedAt_column();

-- Tasks, sources, and generated_clips use snake_case updated_at
CREATE TRIGGER update_tasks_updated_at BEFORE UPDATE ON tasks FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_sources_updated_at BEFORE UPDATE ON sources FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_generated_clips_updated_at BEFORE UPDATE ON generated_clips FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_app_settings_updated_at BEFORE UPDATE ON app_settings FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Better Auth tables use camelCase "updatedAt"
CREATE TRIGGER update_session_updatedAt BEFORE UPDATE ON session FOR EACH ROW EXECUTE FUNCTION update_updatedAt_column();
CREATE TRIGGER update_account_updatedAt BEFORE UPDATE ON account FOR EACH ROW EXECUTE FUNCTION update_updatedAt_column();
CREATE TRIGGER update_verification_updatedAt BEFORE UPDATE ON verification FOR EACH ROW EXECUTE FUNCTION update_updatedAt_column();
