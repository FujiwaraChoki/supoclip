-- Rejected task-creation sources. URLs stored here are already sanitized
-- (scheme, host, path, and a few public query keys only).
CREATE TABLE IF NOT EXISTS rejected_source_attempts (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(255),
    domain VARCHAR(255) NOT NULL,
    sanitized_url TEXT NOT NULL,
    reason TEXT NOT NULL,
    client VARCHAR(128) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_rejected_source_attempts_created_at
    ON rejected_source_attempts (created_at DESC);

CREATE INDEX IF NOT EXISTS ix_rejected_source_attempts_user_id
    ON rejected_source_attempts (user_id);

CREATE INDEX IF NOT EXISTS ix_rejected_source_attempts_domain
    ON rejected_source_attempts (domain);
