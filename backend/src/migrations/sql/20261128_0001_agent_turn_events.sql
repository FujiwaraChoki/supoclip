-- Durable ledger of user-started agent turns for daily, burst, and Pro trial limits.
-- Deleting a conversation cascades agent_runs, so counting from agent_turn_events
-- ensures deleting chats cannot reset rate limits or trial allowances.
-- Undo: DROP TRIGGER IF EXISTS trg_agent_runs_record_turn_event ON agent_runs;
--       DROP FUNCTION IF EXISTS record_agent_turn_event();
--       DROP TABLE IF EXISTS agent_turn_events;

CREATE TABLE IF NOT EXISTS agent_turn_events (
    run_id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_agent_turn_events_user_created
    ON agent_turn_events (user_id, created_at DESC);

-- Install the trigger before backfilling so rows committed by an older release
-- during a rolling deploy are never missed between the two statements.
CREATE OR REPLACE FUNCTION record_agent_turn_event()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO agent_turn_events (run_id, user_id, created_at)
    VALUES (NEW.id, NEW.user_id, NEW.created_at)
    ON CONFLICT (run_id) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_agent_runs_record_turn_event ON agent_runs;
CREATE TRIGGER trg_agent_runs_record_turn_event
    AFTER INSERT ON agent_runs
    FOR EACH ROW
    WHEN (NEW.trigger = 'user')
    EXECUTE FUNCTION record_agent_turn_event();

-- Backfill existing user-started turns so counts stay continuous across deploy.
INSERT INTO agent_turn_events (run_id, user_id, created_at)
SELECT id, user_id, created_at
FROM agent_runs
WHERE trigger = 'user'
ON CONFLICT (run_id) DO NOTHING;
