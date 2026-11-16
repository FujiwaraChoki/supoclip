-- Clip studio state that used to be files on a disk shared by the API and the
-- worker. A worker on another machine sees PostgreSQL, Redis and object storage only.

-- One editing session per (task, clip, clip file). The id is the same hash
-- that names the session's scratch directory and its object storage prefix.
-- No foreign key to tasks: inserting a referencing row waits for a lock on
-- the task row, and a render holds that lock while autosave must keep working.
-- Sessions whose clip is gone are removed by the media cleanup job.
CREATE TABLE IF NOT EXISTS editor_sessions (
    id VARCHAR(64) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL,
    clip_id VARCHAR(36) NOT NULL,
    filename TEXT NOT NULL,
    asset JSONB NOT NULL,
    draft JSONB,
    draft_revision INTEGER NOT NULL DEFAULT 0,
    original JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS editor_sessions_clip_idx ON editor_sessions (clip_id);

-- Export and combine jobs. updated_at is the running worker's heartbeat, and
-- claim identifies the one worker run that may report on the job.
CREATE TABLE IF NOT EXISTS editor_jobs (
    id VARCHAR(32) PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL REFERENCES editor_sessions(id) ON DELETE CASCADE,
    kind VARCHAR(16) NOT NULL CHECK (kind IN ('export', 'combine')),
    status VARCHAR(16) NOT NULL CHECK (status IN ('queued', 'rendering', 'completed', 'cancelled', 'failed')),
    progress INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    error TEXT,
    result JSONB NOT NULL DEFAULT '{}',
    request JSONB NOT NULL DEFAULT '{}',
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    claim VARCHAR(32),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS editor_jobs_session_idx ON editor_jobs (session_id, created_at DESC);

CREATE INDEX IF NOT EXISTS editor_jobs_active_idx ON editor_jobs (updated_at) WHERE status IN ('queued', 'rendering');
