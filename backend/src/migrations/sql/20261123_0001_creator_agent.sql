-- Creator agent: the home screen that does the work for you (docs/creator-agent.md).
--
-- users.home_mode is what the home screen opens on: 'agent', 'manual', or NULL when the
-- person has not chosen yet (the home screen asks once).
-- Undo: DROP TABLE agent_memories, agent_jobs, agent_runs, agent_messages, agent_conversations;
--       ALTER TABLE users DROP COLUMN home_mode; DELETE FROM feature_flags WHERE key = 'creator_agent';
ALTER TABLE users ADD COLUMN IF NOT EXISTS home_mode VARCHAR(10);

-- One chat with the agent, in the workspace it was started in.
CREATE TABLE IF NOT EXISTS agent_conversations (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    team_id VARCHAR(36) REFERENCES teams(id) ON DELETE CASCADE,
    title VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_agent_conversations_user_updated ON agent_conversations (user_id, updated_at DESC);

-- The model's transcript, append-only, exactly as sent and received (content blocks).
-- ``display`` is what the page shows for a row: the person's own words and attachments
-- for user turns, and each tool's result card for tool-result turns.
CREATE TABLE IF NOT EXISTS agent_messages (
    id BIGSERIAL PRIMARY KEY,
    conversation_id VARCHAR(36) NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
    run_id VARCHAR(36),
    role VARCHAR(10) NOT NULL CHECK (role IN ('user', 'assistant')),
    content JSONB NOT NULL,
    display JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_agent_messages_conversation ON agent_messages (conversation_id, id);

-- One turn of the agent: queued, running, waiting for the person to approve an action,
-- or finished. ``pending`` holds the action awaiting approval.
CREATE TABLE IF NOT EXISTS agent_runs (
    id VARCHAR(36) PRIMARY KEY,
    conversation_id VARCHAR(36) NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status VARCHAR(24) NOT NULL DEFAULT 'queued',
    trigger VARCHAR(20) NOT NULL DEFAULT 'user',
    pending JSONB,
    error TEXT,
    cancel_requested BOOLEAN NOT NULL DEFAULT false,
    usage JSONB,
    heartbeat_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_agent_runs_conversation ON agent_runs (conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_agent_runs_active ON agent_runs (status, heartbeat_at)
    WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS ix_agent_runs_user_created ON agent_runs (user_id, created_at);

-- Work the agent left running after its turn: clip edits in the studio, and follow-ups
-- that wake the agent when a project finishes. Any API process may step a job; a claim
-- (claimed_until) keeps two from stepping the same one.
CREATE TABLE IF NOT EXISTS agent_jobs (
    id VARCHAR(36) PRIMARY KEY,
    conversation_id VARCHAR(36) NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    team_id VARCHAR(36) REFERENCES teams(id) ON DELETE CASCADE,
    kind VARCHAR(20) NOT NULL CHECK (kind IN ('clip_edit', 'followup')),
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    step VARCHAR(30) NOT NULL DEFAULT 'start',
    task_id VARCHAR(36),
    clip_id VARCHAR(36),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_check_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    claimed_until TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '1 day'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_agent_jobs_due ON agent_jobs (next_check_at)
    WHERE status IN ('pending', 'running');
CREATE INDEX IF NOT EXISTS ix_agent_jobs_conversation ON agent_jobs (conversation_id, created_at DESC);

-- What the agent remembers about a creator between chats (brand voice, platforms, posting
-- rhythm), written only when the person asks it to remember something or confirms it.
CREATE TABLE IF NOT EXISTS agent_memories (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    note VARCHAR(400) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_agent_memories_user ON agent_memories (user_id, created_at);

-- On for web (Creator plan only, checked in code), off for iOS, which has no agent screen.
INSERT INTO feature_flags (key, description, web_enabled, ios_enabled, admins_bypass)
VALUES (
    'creator_agent',
    'The agent home screen for Creator plans: chat that makes, edits, schedules and publishes clips. Off shows everyone the manual home.',
    true,
    false,
    true
)
ON CONFLICT (key) DO NOTHING;
