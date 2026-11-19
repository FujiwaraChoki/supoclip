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
