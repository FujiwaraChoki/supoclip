-- Self-serve email change (frontend/src/server/email-change.ts). One open
-- request per user: the current address is proven with a code first, then the
-- new one. Codes are stored only as an HMAC and die after 10 minutes, one use
-- or 5 wrong guesses. session_id pins the request to the session that started it.
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
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Rate limit log for email changes: codes sent, codes tried and wrong
-- passwords, per user and per (HMAC'd) client IP. Rows older than a day are pruned.
CREATE TABLE IF NOT EXISTS email_change_events (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(36) REFERENCES users(id) ON DELETE CASCADE,
    ip_hash VARCHAR(64),
    action VARCHAR(20) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_email_change_events_user ON email_change_events (user_id, action, created_at);
CREATE INDEX IF NOT EXISTS ix_email_change_events_ip ON email_change_events (ip_hash, action, created_at);
