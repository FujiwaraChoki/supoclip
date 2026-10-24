CREATE TABLE IF NOT EXISTS chatgpt_host (
    singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
    host_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chatgpt_accounts (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_id TEXT NOT NULL,
    subject TEXT,
    email TEXT NOT NULL,
    encrypted_tokens TEXT,
    model TEXT,
    enabled BOOLEAN NOT NULL DEFAULT false,
    welcome_seen BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (user_id, client_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chatgpt_active_user
ON chatgpt_accounts(user_id) WHERE enabled;

CREATE TABLE IF NOT EXISTS chatgpt_oauth_attempts (
    state_hash TEXT PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    encrypted_attempt TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
