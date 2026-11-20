-- Catch-up for databases created from an older init.sql (or before the table existed).
-- Idempotent: safe on a fresh install and on an existing one. init.sql ends with this
-- exact block; frontend/src/lib/schema-drift.test.ts keeps the two identical and keeps
-- init.sql in step with frontend/prisma/schema.prisma.
-- On an existing install: docker compose exec -T postgres psql -U supoclip -d supoclip < scripts/selfhost-schema-repair.sql

ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_account_id VARCHAR(255);

ALTER TABLE stripe_webhook_events ADD COLUMN IF NOT EXISTS claim_token VARCHAR(255);
ALTER TABLE stripe_webhook_events ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE stripe_webhook_events ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'processed';

CREATE TABLE IF NOT EXISTS ai_edit_usage_events (
    job_id VARCHAR(32) PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    refunded_at TIMESTAMP WITH TIME ZONE
);
CREATE INDEX IF NOT EXISTS ix_ai_edit_usage_events_user_created ON ai_edit_usage_events (user_id, created_at);

CREATE TABLE IF NOT EXISTS email_deliveries (
    message_id VARCHAR(255) PRIMARY KEY,
    recipient VARCHAR(255) NOT NULL,
    subject TEXT NOT NULL,
    region VARCHAR(64) NOT NULL,
    sent_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_email_deliveries_recipient ON email_deliveries (recipient);

CREATE TABLE IF NOT EXISTS minute_usage_events (
    id VARCHAR(36) PRIMARY KEY,
    user_id TEXT NOT NULL,
    task_id TEXT,
    minutes INTEGER NOT NULL,
    source_duration_seconds INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    refunded_at TIMESTAMP WITH TIME ZONE
);
CREATE INDEX IF NOT EXISTS ix_minute_usage_events_user_created ON minute_usage_events (user_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS minute_usage_events_task_id_key ON minute_usage_events (task_id);

CREATE TABLE IF NOT EXISTS service_statuses (
    id VARCHAR(80) PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    status VARCHAR(20) NOT NULL,
    headline VARCHAR(120) NOT NULL,
    description VARCHAR(2000) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stripe_account_migrations (
    id VARCHAR(255) PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_account VARCHAR(255) NOT NULL,
    source_customer VARCHAR(255) NOT NULL,
    source_subscription VARCHAR(255) NOT NULL,
    destination_account VARCHAR(255) NOT NULL,
    destination_customer VARCHAR(255) NOT NULL,
    destination_subscription VARCHAR(255),
    destination_schedule VARCHAR(255),
    stage VARCHAR(32) NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    paid_through TIMESTAMP WITH TIME ZONE NOT NULL,
    source_stopped_verified BOOLEAN NOT NULL DEFAULT false,
    source_resumed_verified BOOLEAN NOT NULL DEFAULT false,
    destination_disabled_verified BOOLEAN NOT NULL DEFAULT false,
    destination_paid_invoice VARCHAR(255),
    cancellation_requested BOOLEAN NOT NULL DEFAULT false,
    recovery_required BOOLEAN NOT NULL DEFAULT false,
    review_reason TEXT,
    livemode BOOLEAN NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS stripe_account_migrations_user_id_key ON stripe_account_migrations (user_id);
CREATE UNIQUE INDEX IF NOT EXISTS stripe_account_migrations_source_account_source_customer_key
    ON stripe_account_migrations (source_account, source_customer);
CREATE UNIQUE INDEX IF NOT EXISTS stripe_account_migrations_destination_account_destination_c_key
    ON stripe_account_migrations (destination_account, destination_customer);

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
    metadata JSONB NOT NULL DEFAULT '{}',
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    last_error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    revoked_at TIMESTAMP WITH TIME ZONE
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_social_accounts_unique_account
    ON social_accounts (user_id, provider, external_account_id);
CREATE INDEX IF NOT EXISTS idx_social_accounts_user_id ON social_accounts (user_id);

CREATE TABLE IF NOT EXISTS social_oauth_states (
    state VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(20) NOT NULL,
    code_verifier VARCHAR(255),
    redirect_uri TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_social_oauth_states_expires_at ON social_oauth_states (expires_at);

CREATE TABLE IF NOT EXISTS social_posts (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id VARCHAR(36) REFERENCES tasks(id) ON DELETE SET NULL,
    clip_id VARCHAR(36),
    social_account_id VARCHAR(36) REFERENCES social_accounts(id) ON DELETE SET NULL,
    provider VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    scheduled_for TIMESTAMP WITH TIME ZONE,
    title VARCHAR(255),
    caption TEXT,
    hashtags TEXT[] DEFAULT ARRAY[]::TEXT[],
    privacy_level VARCHAR(40) NOT NULL DEFAULT 'public',
    media_token VARCHAR(64) UNIQUE,
    media_token_expires_at TIMESTAMP WITH TIME ZONE,
    source_file_path TEXT,
    pending_handle VARCHAR(255),
    external_post_id VARCHAR(255),
    external_url TEXT,
    error_message TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    claimed_at TIMESTAMP WITH TIME ZONE,
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
CREATE INDEX IF NOT EXISTS idx_social_posts_clip_id ON social_posts (clip_id);
CREATE INDEX IF NOT EXISTS idx_social_posts_status_scheduled ON social_posts (status, scheduled_for);
CREATE INDEX IF NOT EXISTS idx_social_posts_task_id ON social_posts (task_id);
CREATE INDEX IF NOT EXISTS idx_social_posts_user_id ON social_posts (user_id);

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
CREATE INDEX IF NOT EXISTS idx_social_post_metrics_post_id ON social_post_metrics (post_id, captured_at DESC);
