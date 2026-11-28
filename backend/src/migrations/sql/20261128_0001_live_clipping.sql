-- Live clipping (docs/live-clipping.md): clip a stream that is still live, on the Creator plan.
--
-- A one-off "last N minutes" part is an ordinary task. Continuous clipping is a live
-- session: the cron worker (advance_live_sessions) adds a part task every interval while
-- the stream is live, and one last part after it ends.
-- Undo: DROP TABLE live_session_parts, live_sessions; DELETE FROM feature_flags WHERE key = 'live_clipping';

CREATE TABLE IF NOT EXISTS live_sessions (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    team_id VARCHAR(36) REFERENCES teams(id) ON DELETE CASCADE,
    platform VARCHAR(16) NOT NULL CHECK (platform IN ('youtube', 'twitch', 'kick')),
    -- What is polled for "still live?" and the recording parts are cut from.
    channel_url TEXT NOT NULL,
    channel VARCHAR(200),
    title TEXT,
    recording_id VARCHAR(100) NOT NULL,
    recording_url TEXT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'ended', 'stopped', 'failed')),
    -- Why a session is no longer active: user, ended, minutes, max_hours, plan, errors.
    stop_reason VARCHAR(40),
    -- The create-task options (captions, fonts, layout, ...) every part reuses.
    options JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Recording seconds already handed to a part.
    covered_until_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    parts_created INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    next_check_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- A cron run owns a session until then, so two runs never add the same part.
    claimed_until TIMESTAMPTZ,
    stream_started_at TIMESTAMPTZ,
    last_checked_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_live_sessions_due ON live_sessions (next_check_at) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS ix_live_sessions_user ON live_sessions (user_id, created_at DESC);
-- One active session per stream recording and workspace.
CREATE UNIQUE INDEX IF NOT EXISTS ux_live_sessions_active_recording
    ON live_sessions (user_id, COALESCE(team_id, ''), platform, recording_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS live_session_parts (
    session_id VARCHAR(36) NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
    part_number INTEGER NOT NULL,
    task_id VARCHAR(36) REFERENCES tasks(id) ON DELETE SET NULL,
    start_seconds DOUBLE PRECISION NOT NULL,
    end_seconds DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (session_id, part_number)
);
CREATE INDEX IF NOT EXISTS ix_live_session_parts_task ON live_session_parts (task_id);

-- Off on web and iOS until the YouTube canary passes; admins bypass it.
INSERT INTO feature_flags (key, description, web_enabled, ios_enabled, admins_bypass)
VALUES (
    'live_clipping',
    'Clip streams that are still live (YouTube, Twitch, Kick) on the Creator plan: the last N minutes, or continuously while live.',
    false,
    false,
    true
)
ON CONFLICT (key) DO NOTHING;
