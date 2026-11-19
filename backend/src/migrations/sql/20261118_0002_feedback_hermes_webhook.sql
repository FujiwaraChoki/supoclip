-- Fail startup transaction safely instead of waiting indefinitely on live writes.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

-- "hermes": the operator chat's webhook route, configured on the server
-- (HERMES_FEEDBACK_WEBHOOK_URL + HERMES_FEEDBACK_WEBHOOK_SECRET), never entered by an
-- admin. The API keeps at most one such row in step with the environment at start.
ALTER TABLE feedback_webhooks DROP CONSTRAINT IF EXISTS feedback_webhooks_kind_check;
ALTER TABLE feedback_webhooks ADD CONSTRAINT feedback_webhooks_kind_check
    CHECK (kind IN ('discord', 'json', 'hermes'));
CREATE UNIQUE INDEX IF NOT EXISTS uq_feedback_webhooks_hermes ON feedback_webhooks (kind) WHERE kind = 'hermes';

SET LOCAL lock_timeout = DEFAULT;
SET LOCAL statement_timeout = DEFAULT;
