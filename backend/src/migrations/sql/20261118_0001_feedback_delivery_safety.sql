-- Fail startup transaction safely instead of waiting indefinitely on live writes.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

-- Fencing prevents a late sender from replacing a newer delivery result.
ALTER TABLE feedback_webhook_deliveries ADD COLUMN IF NOT EXISTS lease_token VARCHAR(36);

-- ON DELETE SET NULL updates the submission in the same transaction as the user
-- deletion. A trigger there clears snapshots for every deletion path, including
-- direct SQL, and cannot miss a concurrent submission protected by its user FK.
CREATE OR REPLACE FUNCTION anonymize_feedback_snapshot() RETURNS trigger
LANGUAGE plpgsql AS $feedback$
BEGIN
    IF NEW.user_id IS NULL THEN
        NEW.email := NULL;
    END IF;
    RETURN NEW;
END;
$feedback$;
DROP TRIGGER IF EXISTS feedback_anonymize_snapshot ON feedback_submissions;
CREATE TRIGGER feedback_anonymize_snapshot
BEFORE INSERT OR UPDATE OF user_id ON feedback_submissions
FOR EACH ROW EXECUTE FUNCTION anonymize_feedback_snapshot();

-- Also repair any previously orphaned snapshots when upgrading the PR schema.
UPDATE feedback_submissions SET email = NULL WHERE user_id IS NULL AND email IS NOT NULL;

SET LOCAL lock_timeout = DEFAULT;
SET LOCAL statement_timeout = DEFAULT;
