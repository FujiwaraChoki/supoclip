-- Same DDL as frontend/prisma/migrations/20261121100000_team_social_account_owner; both runners are idempotent.
-- A social account connected in a team workspace belongs to the team, not to the member
-- who connected it: its user_id is NULL. The unique index on (user_id, provider,
-- external_account_id) then only constrains personal accounts (NULLs never collide), so one
-- person can connect the same channel personally and in a team, and the previous release's
-- ON CONFLICT upsert keeps working against the unchanged index.
ALTER TABLE social_accounts ALTER COLUMN user_id DROP NOT NULL;
UPDATE social_accounts SET user_id = NULL WHERE team_id IS NOT NULL AND user_id IS NOT NULL;
