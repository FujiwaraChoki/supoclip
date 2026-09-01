CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Connected social accounts (one row per user/provider/external account).
CREATE TABLE IF NOT EXISTS social_accounts (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(20) NOT NULL,
    external_account_id VARCHAR(255) NOT NULL,
    display_name VARCHAR(255),
    username VARCHAR(255),
    avatar_url TEXT,
    access_token_encrypted TEXT NOT NULL,
    refresh_token_encrypted TEXT,
    token_expires_at TIMESTAMP WITH TIME ZONE,
    scopes TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    last_error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    revoked_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_social_accounts_user_id ON social_accounts(user_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_social_accounts_unique_account
ON social_accounts(user_id, provider, external_account_id);

-- Short-lived OAuth state records (CSRF protection + PKCE verifier storage).
CREATE TABLE IF NOT EXISTS social_oauth_states (
    state VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(20) NOT NULL,
    code_verifier VARCHAR(255),
    redirect_uri TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_social_oauth_states_expires_at
ON social_oauth_states(expires_at);

-- One row per publish attempt of a clip to a connected account.
-- Clip attributes are snapshotted so the performance loop survives clip
-- deletion and later re-edits.
CREATE TABLE IF NOT EXISTS social_posts (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id VARCHAR(36) REFERENCES tasks(id) ON DELETE SET NULL,
    clip_id VARCHAR(36) REFERENCES generated_clips(id) ON DELETE SET NULL,
    social_account_id VARCHAR(36) REFERENCES social_accounts(id) ON DELETE SET NULL,
    provider VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    scheduled_for TIMESTAMP WITH TIME ZONE,
    title VARCHAR(255),
    caption TEXT,
    hashtags TEXT[] NOT NULL DEFAULT '{}',
    privacy_level VARCHAR(40) NOT NULL DEFAULT 'public',
    media_token VARCHAR(64) UNIQUE,
    media_token_expires_at TIMESTAMP WITH TIME ZONE,
    source_file_path TEXT,
    pending_handle VARCHAR(255),
    external_post_id VARCHAR(255),
    external_url TEXT,
    error_message TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    hook_type VARCHAR(50),
    hook_title VARCHAR(255),
    virality_score INTEGER,
    hook_score INTEGER,
    engagement_score INTEGER,
    value_score INTEGER,
    shareability_score INTEGER,
    clip_duration DOUBLE PRECISION,
    published_at TIMESTAMP WITH TIME ZONE,
    last_metrics_at TIMESTAMP WITH TIME ZONE,
    metrics JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_social_posts_user_id ON social_posts(user_id);

CREATE INDEX IF NOT EXISTS idx_social_posts_clip_id ON social_posts(clip_id);

CREATE INDEX IF NOT EXISTS idx_social_posts_task_id ON social_posts(task_id);

CREATE INDEX IF NOT EXISTS idx_social_posts_status_scheduled
ON social_posts(status, scheduled_for);

-- Time series of engagement metrics pulled back from each platform.
CREATE TABLE IF NOT EXISTS social_post_metrics (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    post_id VARCHAR(36) NOT NULL REFERENCES social_posts(id) ON DELETE CASCADE,
    captured_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    views BIGINT,
    likes BIGINT,
    comments BIGINT,
    shares BIGINT,
    saves BIGINT,
    raw JSONB
);

CREATE INDEX IF NOT EXISTS idx_social_post_metrics_post_id
ON social_post_metrics(post_id, captured_at DESC)
