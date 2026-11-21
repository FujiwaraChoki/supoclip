-- Multi-tenant Team plan, memberships, invitations, and nullable team_id scoping.

CREATE TABLE IF NOT EXISTS teams (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    name VARCHAR(120) NOT NULL,
    slug VARCHAR(80) NOT NULL UNIQUE,
    owner_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan VARCHAR(20) NOT NULL DEFAULT 'free',
    subscription_status VARCHAR(20) NOT NULL DEFAULT 'inactive',
    subscription_provider VARCHAR(20),
    stripe_customer_id VARCHAR(255) UNIQUE,
    stripe_account_id VARCHAR(255),
    stripe_subscription_id VARCHAR(255) UNIQUE,
    seat_limit INTEGER NOT NULL DEFAULT 3 CHECK (seat_limit >= 1),
    billing_period_start TIMESTAMP WITH TIME ZONE,
    billing_period_end TIMESTAMP WITH TIME ZONE,
    subscription_cancel_at TIMESTAMP WITH TIME ZONE,
    pending_plan VARCHAR(20),
    pending_plan_invoice_id VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_teams_owner_id ON teams (owner_id);

CREATE TABLE IF NOT EXISTS team_members (
    team_id VARCHAR(36) NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (team_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_team_members_user_id ON team_members (user_id);

CREATE TABLE IF NOT EXISTS team_invitations (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    team_id VARCHAR(36) NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    invited_by VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    accepted_at TIMESTAMP WITH TIME ZONE,
    revoked_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_team_invitations_team_email ON team_invitations (team_id, email);
CREATE INDEX IF NOT EXISTS ix_team_invitations_email ON team_invitations (email);

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS team_id VARCHAR(36) REFERENCES teams(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_tasks_team_id ON tasks (team_id);

ALTER TABLE minute_usage_events ADD COLUMN IF NOT EXISTS team_id VARCHAR(36);
CREATE INDEX IF NOT EXISTS ix_minute_usage_events_team_created ON minute_usage_events (team_id, created_at);

ALTER TABLE ai_edit_usage_events ADD COLUMN IF NOT EXISTS team_id VARCHAR(36);
CREATE INDEX IF NOT EXISTS ix_ai_edit_usage_events_team_created ON ai_edit_usage_events (team_id, created_at);

ALTER TABLE social_accounts ADD COLUMN IF NOT EXISTS team_id VARCHAR(36) REFERENCES teams(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_social_accounts_team_id ON social_accounts (team_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_social_accounts_team_unique
    ON social_accounts (team_id, provider, external_account_id)
    WHERE team_id IS NOT NULL;

ALTER TABLE social_oauth_states ADD COLUMN IF NOT EXISTS team_id VARCHAR(36) REFERENCES teams(id) ON DELETE CASCADE;

ALTER TABLE social_posts ADD COLUMN IF NOT EXISTS team_id VARCHAR(36) REFERENCES teams(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_social_posts_team_id ON social_posts (team_id);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'api_keys') THEN
        ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS team_id VARCHAR(36) REFERENCES teams(id) ON DELETE CASCADE;
        CREATE INDEX IF NOT EXISTS idx_api_keys_team_id ON api_keys (team_id);
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'media_upload_sessions') THEN
        ALTER TABLE media_upload_sessions ADD COLUMN IF NOT EXISTS team_id VARCHAR(36);
    END IF;
END $$;
