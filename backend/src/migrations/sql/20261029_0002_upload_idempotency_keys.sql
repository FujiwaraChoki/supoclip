-- A completed upload can be retried under more than one Idempotency-Key.
-- The session column keeps the first key. Later keys stay in this table so
-- a different file sent with an earlier key still conflicts.

CREATE TABLE IF NOT EXISTS media_upload_idempotency_keys (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES media_upload_sessions(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS media_upload_idempotency_keys_session_idx
    ON media_upload_idempotency_keys (session_id);

INSERT INTO media_upload_idempotency_keys (user_id, idempotency_key, session_id)
SELECT user_id, idempotency_key, id
FROM media_upload_sessions
WHERE idempotency_key IS NOT NULL AND state = 'completed'
ON CONFLICT DO NOTHING;
