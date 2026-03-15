CREATE TABLE IF NOT EXISTS youtube_cookie_accounts (
    id VARCHAR(36) PRIMARY KEY DEFAULT gen_random_uuid()::text,
    label VARCHAR(255) NOT NULL,
    email_hint VARCHAR(255),
    status VARCHAR(32) NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    playwright_storage_state_path TEXT,
    yt_dlp_cookiefile_path TEXT,
    last_used_at TIMESTAMP WITH TIME ZONE,
    last_verified_at TIMESTAMP WITH TIME ZONE,
    last_refresh_started_at TIMESTAMP WITH TIME ZONE,
    last_refresh_completed_at TIMESTAMP WITH TIME ZONE,
    consecutive_auth_failures INTEGER NOT NULL DEFAULT 0,
    cooldown_until TIMESTAMP WITH TIME ZONE,
    last_error_code VARCHAR(80),
    last_error_message TEXT,
    created_by_user_id VARCHAR(36),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_youtube_cookie_accounts_status
    ON youtube_cookie_accounts(status, priority, last_used_at);

CREATE TABLE IF NOT EXISTS youtube_cookie_events (
    id VARCHAR(36) PRIMARY KEY DEFAULT gen_random_uuid()::text,
    account_id VARCHAR(36) NOT NULL REFERENCES youtube_cookie_accounts(id) ON DELETE CASCADE,
    event_type VARCHAR(80) NOT NULL,
    status VARCHAR(32) NOT NULL,
    task_id VARCHAR(36),
    message TEXT,
    metadata_json TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_youtube_cookie_events_account_created
    ON youtube_cookie_events(account_id, created_at DESC);
