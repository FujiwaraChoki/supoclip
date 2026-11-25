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
    notify_on_publish BOOLEAN NOT NULL DEFAULT false,
    default_clip_length VARCHAR(10) NOT NULL DEFAULT 'auto'
        CONSTRAINT users_default_clip_length_check CHECK (default_clip_length IN ('auto', 'short', 'medium', 'long')),
    create_settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    product_updates_unsubscribed_at TIMESTAMP WITH TIME ZONE,
    -- Monetization and billing fields
    is_admin BOOLEAN NOT NULL DEFAULT false,
    plan VARCHAR(20) NOT NULL DEFAULT 'free',
    subscription_status VARCHAR(20) NOT NULL DEFAULT 'inactive',
    subscription_provider VARCHAR(20),
    stripe_customer_id VARCHAR(255) UNIQUE,
    stripe_account_id VARCHAR(255),
    stripe_subscription_id VARCHAR(255) UNIQUE,
    billing_period_start TIMESTAMP WITH TIME ZONE,
    billing_period_end TIMESTAMP WITH TIME ZONE,
    trial_ends_at TIMESTAMP WITH TIME ZONE,
    subscription_cancel_at TIMESTAMP WITH TIME ZONE,
    -- Pro subscribers from before the 720p change keep 1080p while subscribed
    legacy_hd BOOLEAN NOT NULL DEFAULT false,
    -- Higher tier chosen in the billing portal, granted only after its invoice is paid.
    pending_plan VARCHAR(20),
    pending_plan_invoice_id VARCHAR(255)
);

-- Idempotency-Key for task creation. A key names one create request for its
-- user, and a repeat within the retention window returns the task it made
-- instead of a second charged one. The row is inserted in the task's own
-- transaction. task_id has no foreign key: like minute_usage_events, deleting
-- the task must not free the key and let a late retry charge again.
CREATE TABLE IF NOT EXISTS task_idempotency_keys (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    task_id TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, idempotency_key)
);

-- Self-serve email change (frontend/src/server/email-change.ts). One open
-- request per user: the current address is proven with a code first, then the
-- new one. Codes are stored only as an HMAC and die after 10 minutes, one use
-- or 5 wrong guesses. session_id pins the request to the session that started it;
-- finish_claimed_at lets one finish run at a time.
CREATE TABLE IF NOT EXISTS email_change_requests (
    user_id VARCHAR(36) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    session_id VARCHAR(36) NOT NULL,
    stage VARCHAR(20) NOT NULL CHECK (stage IN ('verify_current', 'enter_new', 'verify_new', 'finishing')),
    current_email VARCHAR(255) NOT NULL,
    new_email VARCHAR(255),
    code_hash VARCHAR(64),
    code_sent_at TIMESTAMP WITH TIME ZONE,
    code_expires_at TIMESTAMP WITH TIME ZONE,
    code_attempts INTEGER NOT NULL DEFAULT 0,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    finish_claimed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Rate limit log for email changes: codes sent, codes tried and passwords
-- checked, per user, per (HMAC'd) client IP and, for codes to a new address,
-- per (HMAC'd) address. Rows older than a day are pruned.
CREATE TABLE IF NOT EXISTS email_change_events (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(36) REFERENCES users(id) ON DELETE CASCADE,
    ip_hash VARCHAR(64),
    target_hash VARCHAR(64),
    action VARCHAR(20) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_email_change_events_user ON email_change_events (user_id, action, created_at);
CREATE INDEX IF NOT EXISTS ix_email_change_events_ip ON email_change_events (ip_hash, action, created_at);
CREATE INDEX IF NOT EXISTS ix_email_change_events_target ON email_change_events (target_hash, action, created_at);

-- Every completed email change, for support ("this wasn't me") and so the
-- backend sends the notice to the previous address only once per real change.
-- Removed with the account.
CREATE TABLE IF NOT EXISTS email_change_log (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    old_email VARCHAR(255) NOT NULL,
    new_email VARCHAR(255) NOT NULL,
    session_id VARCHAR(36) NOT NULL,
    ip_hash VARCHAR(64),
    changed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notified_at TIMESTAMP WITH TIME ZONE
);
CREATE INDEX IF NOT EXISTS ix_email_change_log_user ON email_change_log (user_id, changed_at);

-- Source table (created before tasks since tasks reference sources)
CREATE TABLE sources (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    type VARCHAR(20) CONSTRAINT check_source_type CHECK (type IN ('youtube', 'video_url', 'twitch', 'kick')) NOT NULL,
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
    source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Source length charged against the monthly minute allowance
    source_duration_seconds INTEGER,

    -- Font customization fields
    font_family VARCHAR(100) DEFAULT 'TikTokSans-Regular',
    font_size INTEGER DEFAULT 24,
    font_color VARCHAR(7) DEFAULT '#FFFFFF', -- Hex color code

    -- Caption template and B-roll options
    caption_template VARCHAR(50) DEFAULT 'default',
    include_broll BOOLEAN DEFAULT false,
    processing_mode VARCHAR(20) NOT NULL DEFAULT 'fast',
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    cache_hit BOOLEAN NOT NULL DEFAULT false,
    error_code VARCHAR(80),
    stage_timings_json TEXT,
    completion_notification_sent_at TIMESTAMP WITH TIME ZONE,
    share_token VARCHAR(64),
    share_enabled BOOLEAN NOT NULL DEFAULT false,

    -- Workflow: what the task makes, its options and the persisted plan
    workflow VARCHAR(20) NOT NULL DEFAULT 'highlights'
        CONSTRAINT tasks_workflow_check CHECK (workflow IN ('highlights', 'series', 'topic', 'recap', 'trailer')),
    workflow_options JSONB NOT NULL DEFAULT '{}'::jsonb,
    workflow_plan JSONB,

    -- Dynamic edits: EditStyle.to_json(), non-default keys only. NULL = off
    edit_style JSONB,

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

    -- Workflow role (part, topic, recap, trailer), series part number, plan metadata
    workflow_role VARCHAR(20),
    part_number INTEGER,
    workflow_meta JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Dynamic edits summary {fx_level, timing, intensity, template}. NULL = legacy clip
    fx_meta JSONB,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

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
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'processed',
    claim_token VARCHAR(255),
    lease_expires_at TIMESTAMPTZ
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

-- OAuth clients registered with the hosted MCP server (claude.ai, ChatGPT, ...).
-- The access token a client receives is an API key above; no tokens are stored here.
CREATE TABLE mcp_oauth_clients (
    client_id VARCHAR(64) PRIMARY KEY,
    client_secret VARCHAR(128),
    client_name VARCHAR(200),
    metadata JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP WITH TIME ZONE
);
CREATE INDEX idx_mcp_oauth_clients_created_at ON mcp_oauth_clients (created_at);

-- First-touch campaign attribution, written once shortly after sign-up
CREATE TABLE user_acquisition (
    user_id VARCHAR(36) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    utm_source VARCHAR(100),
    utm_medium VARCHAR(100),
    utm_campaign VARCHAR(100),
    utm_content VARCHAR(100),
    utm_term VARCHAR(100),
    ref VARCHAR(100),
    referrer_host VARCHAR(255),
    landing_path VARCHAR(512),
    first_seen_at TIMESTAMP WITH TIME ZONE,
    first_clip_exported_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Creator (affiliate) program: one application per user, reviewed by admins
CREATE TABLE affiliates (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    slug VARCHAR(20) UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    platform VARCHAR(20) NOT NULL,
    profile_url VARCHAR(500) NOT NULL,
    audience_size VARCHAR(20) NOT NULL,
    video_url VARCHAR(500),
    promotion_plan VARCHAR(1000),
    decline_reason VARCHAR(1000),
    stripe_promotion_code_id VARCHAR(255),
    app_store_code_added_at TIMESTAMP WITH TIME ZONE,
    terms_accepted_at TIMESTAMP WITH TIME ZONE NOT NULL,
    reviewed_at TIMESTAMP WITH TIME ZONE,
    reviewed_by VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    -- Stripe Connect Express account that receives commission payouts
    stripe_connect_account_id VARCHAR(255),
    stripe_connect_platform_id VARCHAR(255),
    payout_country VARCHAR(2),
    payouts_enabled BOOLEAN NOT NULL DEFAULT false,
    -- Where the creator lives; commission only where Stripe can pay ("ZZ" = elsewhere)
    country VARCHAR(2),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Creator commissions (frontend/src/server/affiliate-commissions.ts): the creator behind
-- each referred Stripe subscription, a ledger of commission per paid invoice (negative
-- rows for refunds/disputes), and the Connect transfers that paid it out.
CREATE TABLE affiliate_referrals (
    id VARCHAR(36) PRIMARY KEY,
    affiliate_user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    customer_user_id VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    stripe_account_id VARCHAR(255) NOT NULL,
    stripe_subscription_id VARCHAR(255) NOT NULL UNIQUE,
    code VARCHAR(20) NOT NULL,
    first_paid_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE affiliate_payouts (
    id VARCHAR(36) PRIMARY KEY,
    affiliate_user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    currency VARCHAR(3) NOT NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    stripe_platform_id VARCHAR(255) NOT NULL,
    stripe_connect_account_id VARCHAR(255) NOT NULL,
    stripe_transfer_id VARCHAR(255),
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    failure_reason VARCHAR(500),
    created_by VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE affiliate_commissions (
    id VARCHAR(36) PRIMARY KEY,
    idempotency_key VARCHAR(255) NOT NULL UNIQUE,
    affiliate_user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    referral_id VARCHAR(36) NOT NULL REFERENCES affiliate_referrals(id) ON DELETE CASCADE,
    kind VARCHAR(20) NOT NULL,
    stripe_account_id VARCHAR(255) NOT NULL,
    stripe_invoice_id VARCHAR(255),
    stripe_charge_id VARCHAR(255),
    currency VARCHAR(3) NOT NULL,
    base_amount INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    rate_bps INTEGER NOT NULL,
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
    available_at TIMESTAMP WITH TIME ZONE NOT NULL,
    payout_id VARCHAR(36) REFERENCES affiliate_payouts(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS "announcements" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "title" VARCHAR(120) NOT NULL,
    "description" VARCHAR(2000) NOT NULL,
    "href" VARCHAR(500) NOT NULL,
    "action" VARCHAR(80) NOT NULL,
    "is_published" BOOLEAN NOT NULL DEFAULT false,
    "published_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS "announcements_is_published_published_at_idx"
    ON "announcements" ("is_published", "published_at");

-- Preserve the first announcement's ID so existing read badges stay cleared.
INSERT INTO "announcements" ("id", "title", "description", "href", "action", "is_published", "published_at")
VALUES (
    'creator-program-2026-10',
    'The creator program is open',
    'Make content about SupoClip and get hosted Pro for free while participating as an approved creator. Your audience gets 20% off their first three months. No cash payments or referral commissions.',
    '/affiliate',
    'Explore the creator program',
    true,
    '2026-10-09T00:00:00Z'
)
ON CONFLICT ("id") DO NOTHING;

-- Create indexes for better performance
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX user_acquisition_utm_source_utm_campaign_idx ON user_acquisition(utm_source, utm_campaign);
CREATE INDEX affiliates_status_created_at_idx ON affiliates(status, created_at);
CREATE INDEX affiliate_referrals_affiliate_user_id_idx ON affiliate_referrals(affiliate_user_id);
CREATE INDEX affiliate_payouts_affiliate_user_id_created_at_idx ON affiliate_payouts(affiliate_user_id, created_at);
CREATE INDEX affiliate_payouts_status_idx ON affiliate_payouts(status);
CREATE INDEX affiliate_commissions_affiliate_user_id_payout_id_idx ON affiliate_commissions(affiliate_user_id, payout_id);
CREATE INDEX affiliate_commissions_stripe_charge_id_idx ON affiliate_commissions(stripe_charge_id);
CREATE INDEX idx_tasks_user_id ON tasks(user_id);
CREATE INDEX idx_tasks_source_id ON tasks(source_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_created_at ON tasks(created_at);
CREATE INDEX idx_tasks_processing_mode ON tasks(processing_mode);
CREATE INDEX idx_tasks_completed_at ON tasks(completed_at);
CREATE INDEX idx_tasks_user_workflow ON tasks(user_id, workflow);
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
-- One row per account and feature tour (frontend/src/lib/feature-tours.ts).
-- A row means the tour was shown, and how it ended says whether it helped.
CREATE TABLE user_feature_tours (
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tour_id VARCHAR(64) NOT NULL,
    shown_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    dismissed_at TIMESTAMP WITH TIME ZONE,
    -- The last step reached, counted from the first real step, or null until the tour ends.
    last_step INTEGER,
    PRIMARY KEY (user_id, tour_id)
);
CREATE INDEX ix_user_feature_tours_tour ON user_feature_tours (tour_id);
-- Feature flags, switched per platform from /admin without a deploy.
-- Read by backend/src/feature_flags.py, which caches them for a few seconds.
-- admins_bypass keeps a flag on for admins on every platform while it is off for everyone else.
CREATE TABLE feature_flags (
    key VARCHAR(64) PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    web_enabled BOOLEAN NOT NULL DEFAULT false,
    ios_enabled BOOLEAN NOT NULL DEFAULT false,
    admins_bypass BOOLEAN NOT NULL DEFAULT true,
    updated_by VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_feature_flags_updated_by ON feature_flags (updated_by);
-- Workflows stay off on web and iOS until the iOS build that has them is approved.
INSERT INTO feature_flags (key, description, web_enabled, ios_enabled, admins_bypass)
VALUES (
    'workflows',
    'Series, topic, recap and trailer, plus instructions and Advanced settings. The WORKFLOWS_ACCESS env gate must allow them too.',
    false,
    false,
    true
);
INSERT INTO feature_flags (key, description, web_enabled, ios_enabled, admins_bypass)
VALUES (
    'tiktok_publishing',
    'TikTok connecting and publishing. Off shows TikTok as paused with a short notice. Turn on once TikTok''s Direct Post audit passes.',
    false,
    false,
    false
);
-- Dynamic edits stay off on web and iOS until rollout. Admins bypass the flag.
INSERT INTO feature_flags (key, description, web_enabled, ios_enabled, admins_bypass)
VALUES (
    'dynamic_edits',
    'Edit intensity, SFX, music and animated caption styles',
    false,
    false,
    true
);
-- Instagram Reels publishing stays off on web and iOS until Meta App Review passes. Admins bypass the flag.
INSERT INTO feature_flags (key, description, web_enabled, ios_enabled, admins_bypass)
VALUES (
    'instagram_publishing',
    'Instagram Reels connecting and publishing. Off hides Instagram as coming soon while letting admins test.',
    false,
    false,
    true
);
-- The agent home screen: on for web (Creator plan only, checked in code), off for iOS.
INSERT INTO feature_flags (key, description, web_enabled, ios_enabled, admins_bypass)
VALUES (
    'creator_agent',
    'The agent home screen for Creator plans: chat that makes, edits, schedules and publishes clips. Off shows everyone the manual home.',
    true,
    false,
    true
);
-- Onboarding guided by Agent with a free preview clip: on for web, off for iOS.
INSERT INTO feature_flags (key, description, web_enabled, ios_enabled, admins_bypass)
VALUES (
    'pebble_onboarding',
    'New accounts: the onboarding guided by Agent, with one free preview clip from their own video (A/B against the classic onboarding).',
    true,
    false,
    true
);

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
    user_id VARCHAR(36) REFERENCES users(id) ON DELETE CASCADE,
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
    publish_notification_sent_at TIMESTAMP WITH TIME ZONE,
    last_metrics_at TIMESTAMP WITH TIME ZONE,
    metrics JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_social_posts_clip_id ON social_posts (clip_id);
CREATE INDEX IF NOT EXISTS idx_social_posts_status_scheduled ON social_posts (status, scheduled_for);
CREATE INDEX IF NOT EXISTS idx_social_posts_task_id ON social_posts (task_id);
CREATE INDEX IF NOT EXISTS idx_social_posts_user_id ON social_posts (user_id);
ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS publish_notification_sent_at TIMESTAMP WITH TIME ZONE;

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

CREATE TABLE IF NOT EXISTS teams (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    name VARCHAR(120) NOT NULL,
    slug VARCHAR(80) NOT NULL UNIQUE,
    owner_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan VARCHAR(20) NOT NULL DEFAULT 'free',
    subscription_status VARCHAR(20) NOT NULL DEFAULT 'inactive',
    subscription_provider VARCHAR(20),
    stripe_customer_id VARCHAR(255) UNIQUE,
    stripe_account_id VARCHAR(255),
    stripe_subscription_id VARCHAR(255) UNIQUE,
    seat_limit INTEGER NOT NULL DEFAULT 3 CHECK (seat_limit >= 1),
    billing_period_start TIMESTAMP WITH TIME ZONE,
    billing_period_end TIMESTAMP WITH TIME ZONE,
    subscription_cancel_at TIMESTAMP WITH TIME ZONE,
    pending_plan VARCHAR(20),
    pending_plan_invoice_id VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_teams_owner_id ON teams (owner_id);

CREATE TABLE IF NOT EXISTS team_members (
    team_id VARCHAR(36) NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
    monthly_minute_limit INTEGER CHECK (monthly_minute_limit IS NULL OR monthly_minute_limit >= 0),
    monthly_ai_edit_limit INTEGER CHECK (monthly_ai_edit_limit IS NULL OR monthly_ai_edit_limit >= 0),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (team_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_team_members_user_id ON team_members (user_id);

CREATE TABLE IF NOT EXISTS team_invitations (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    team_id VARCHAR(36) NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    invited_by VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    accepted_at TIMESTAMP WITH TIME ZONE,
    revoked_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_team_invitations_team_email ON team_invitations (team_id, email);
CREATE INDEX IF NOT EXISTS ix_team_invitations_email ON team_invitations (email);

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS team_id VARCHAR(36) REFERENCES teams(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_tasks_team_id ON tasks (team_id);

ALTER TABLE minute_usage_events ADD COLUMN IF NOT EXISTS team_id VARCHAR(36);
CREATE INDEX IF NOT EXISTS ix_minute_usage_events_team_created ON minute_usage_events (team_id, created_at);

ALTER TABLE ai_edit_usage_events ADD COLUMN IF NOT EXISTS team_id VARCHAR(36);
CREATE INDEX IF NOT EXISTS ix_ai_edit_usage_events_team_created ON ai_edit_usage_events (team_id, created_at);

ALTER TABLE social_accounts ADD COLUMN IF NOT EXISTS team_id VARCHAR(36) REFERENCES teams(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_social_accounts_team_id ON social_accounts (team_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_social_accounts_team_unique ON social_accounts (team_id, provider, external_account_id) WHERE team_id IS NOT NULL;
ALTER TABLE social_accounts ALTER COLUMN user_id DROP NOT NULL;

ALTER TABLE social_oauth_states ADD COLUMN IF NOT EXISTS team_id VARCHAR(36) REFERENCES teams(id) ON DELETE CASCADE;

ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS team_id VARCHAR(36) REFERENCES teams(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_social_posts_team_id ON social_posts (team_id);

ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS team_id VARCHAR(36) REFERENCES teams(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_api_keys_team_id ON api_keys (team_id);

-- What the home screen opens on: 'agent', 'manual', or NULL until the person chooses.
ALTER TABLE users ADD COLUMN IF NOT EXISTS home_mode VARCHAR(10);

-- Pebble onboarding (A/B group, answers, finished) and the one free preview clip (docs/onboarding.md).
ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_variant VARCHAR(16);
ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_answers JSONB;
ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_completed_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS preview_used_at TIMESTAMPTZ;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS preview JSONB;
CREATE INDEX IF NOT EXISTS ix_tasks_preview_created ON tasks (created_at) WHERE preview IS NOT NULL;

-- Creator commissions (affiliate_referrals, affiliate_commissions, affiliate_payouts) and Connect payout columns.
ALTER TABLE "affiliates" ADD COLUMN IF NOT EXISTS "stripe_connect_account_id" VARCHAR(255);
ALTER TABLE "affiliates" ADD COLUMN IF NOT EXISTS "stripe_connect_platform_id" VARCHAR(255);
ALTER TABLE "affiliates" ADD COLUMN IF NOT EXISTS "payout_country" VARCHAR(2);
ALTER TABLE "affiliates" ADD COLUMN IF NOT EXISTS "payouts_enabled" BOOLEAN NOT NULL DEFAULT false;
-- Where the creator lives: commission is only earned where Stripe can pay it ("ZZ" = elsewhere).
ALTER TABLE "affiliates" ADD COLUMN IF NOT EXISTS "country" VARCHAR(2);

CREATE TABLE IF NOT EXISTS "affiliate_referrals" (
    "id" VARCHAR(36) PRIMARY KEY,
    "affiliate_user_id" VARCHAR(36) NOT NULL,
    "customer_user_id" VARCHAR(36),
    "stripe_account_id" VARCHAR(255) NOT NULL,
    "stripe_subscription_id" VARCHAR(255) NOT NULL,
    "code" VARCHAR(20) NOT NULL,
    "first_paid_at" TIMESTAMPTZ NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "affiliate_referrals_affiliate_user_id_fkey"
        FOREIGN KEY ("affiliate_user_id") REFERENCES "users"("id")
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "affiliate_referrals_customer_user_id_fkey"
        FOREIGN KEY ("customer_user_id") REFERENCES "users"("id")
        ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "affiliate_referrals_stripe_subscription_id_key"
    ON "affiliate_referrals"("stripe_subscription_id");
CREATE INDEX IF NOT EXISTS "affiliate_referrals_affiliate_user_id_idx"
    ON "affiliate_referrals"("affiliate_user_id");

CREATE TABLE IF NOT EXISTS "affiliate_payouts" (
    "id" VARCHAR(36) PRIMARY KEY,
    "affiliate_user_id" VARCHAR(36) NOT NULL,
    "currency" VARCHAR(3) NOT NULL,
    "amount" INTEGER NOT NULL CHECK ("amount" > 0),
    "stripe_platform_id" VARCHAR(255) NOT NULL,
    "stripe_connect_account_id" VARCHAR(255) NOT NULL,
    "stripe_transfer_id" VARCHAR(255),
    "status" VARCHAR(20) NOT NULL DEFAULT 'pending',
    "failure_reason" VARCHAR(500),
    "created_by" VARCHAR(36),
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "affiliate_payouts_affiliate_user_id_fkey"
        FOREIGN KEY ("affiliate_user_id") REFERENCES "users"("id")
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "affiliate_payouts_created_by_fkey"
        FOREIGN KEY ("created_by") REFERENCES "users"("id")
        ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE INDEX IF NOT EXISTS "affiliate_payouts_affiliate_user_id_created_at_idx"
    ON "affiliate_payouts"("affiliate_user_id", "created_at");
CREATE INDEX IF NOT EXISTS "affiliate_payouts_status_idx" ON "affiliate_payouts"("status");

CREATE TABLE IF NOT EXISTS "affiliate_commissions" (
    "id" VARCHAR(36) PRIMARY KEY,
    "idempotency_key" VARCHAR(255) NOT NULL,
    "affiliate_user_id" VARCHAR(36) NOT NULL,
    "referral_id" VARCHAR(36) NOT NULL,
    "kind" VARCHAR(20) NOT NULL,
    "stripe_account_id" VARCHAR(255) NOT NULL,
    "stripe_invoice_id" VARCHAR(255),
    "stripe_charge_id" VARCHAR(255),
    "currency" VARCHAR(3) NOT NULL,
    "base_amount" INTEGER NOT NULL,
    "amount" INTEGER NOT NULL,
    "rate_bps" INTEGER NOT NULL,
    "occurred_at" TIMESTAMPTZ NOT NULL,
    "available_at" TIMESTAMPTZ NOT NULL,
    "payout_id" VARCHAR(36),
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "affiliate_commissions_affiliate_user_id_fkey"
        FOREIGN KEY ("affiliate_user_id") REFERENCES "users"("id")
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "affiliate_commissions_referral_id_fkey"
        FOREIGN KEY ("referral_id") REFERENCES "affiliate_referrals"("id")
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "affiliate_commissions_payout_id_fkey"
        FOREIGN KEY ("payout_id") REFERENCES "affiliate_payouts"("id")
        ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS "affiliate_commissions_idempotency_key_key"
    ON "affiliate_commissions"("idempotency_key");
CREATE INDEX IF NOT EXISTS "affiliate_commissions_affiliate_user_id_payout_id_idx"
    ON "affiliate_commissions"("affiliate_user_id", "payout_id");
CREATE INDEX IF NOT EXISTS "affiliate_commissions_stripe_charge_id_idx"
    ON "affiliate_commissions"("stripe_charge_id");
