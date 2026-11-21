// @vitest-environment node
import { readFileSync } from "node:fs";
import { PGlite } from "@electric-sql/pglite";
import { uuid_ossp } from "@electric-sql/pglite/contrib/uuid_ossp";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

const read = (relPath: string) => readFileSync(new URL(relPath, import.meta.url), "utf8");

describe("teams schema, migrations, constraints & pooled SQL (real Postgres)", () => {
  let db: PGlite;

  beforeAll(async () => {
    db = new PGlite({ extensions: { uuid_ossp } });
    await db.exec(read("../../../init.sql"));
    // Verify Prisma migration + backend migration + schema repair are all idempotent
    // when run on top of an existing database (and can re-create dropped team tables).
    await db.exec(`
      DROP TABLE team_invitations CASCADE;
      DROP TABLE team_members CASCADE;
      ALTER TABLE tasks DROP COLUMN team_id;
      ALTER TABLE minute_usage_events DROP COLUMN team_id;
      ALTER TABLE ai_edit_usage_events DROP COLUMN team_id;
      ALTER TABLE social_accounts DROP COLUMN team_id;
      ALTER TABLE social_oauth_states DROP COLUMN team_id;
      ALTER TABLE social_posts DROP COLUMN team_id;
      ALTER TABLE api_keys DROP COLUMN team_id;
      DROP TABLE teams CASCADE;
    `);
    await db.exec(read("../../prisma/migrations/20261120180000_add_teams/migration.sql"));
    // Re-running both Prisma and backend migrations + repair must be 100% idempotent.
    await db.exec(read("../../prisma/migrations/20261120180000_add_teams/migration.sql"));
    await db.exec(read("../../../backend/src/migrations/sql/20261120_0002_teams.sql"));
    await db.exec(read("../../prisma/migrations/20261121020000_team_member_limits/migration.sql"));
    await db.exec(read("../../../backend/src/migrations/sql/20261121_0003_team_member_limits.sql"));
    // A database from before team-owned social accounts: its column is still NOT NULL.
    await db.exec("ALTER TABLE social_accounts ALTER COLUMN user_id SET NOT NULL");
    await db.exec(read("../../prisma/migrations/20261121100000_team_social_account_owner/migration.sql"));
    await db.exec(read("../../../backend/src/migrations/sql/20261121_0004_team_social_account_owner.sql"));
    await db.exec(read("../../../scripts/selfhost-schema-repair.sql"));
  });

  afterAll(async () => {
    await db.close();
  });

  beforeEach(async () => {
    await db.exec(`
      DELETE FROM minute_usage_events;
      DELETE FROM ai_edit_usage_events;
      DELETE FROM generated_clips;
      DELETE FROM tasks;
      DELETE FROM sources;
      DELETE FROM social_posts;
      DELETE FROM social_accounts;
      DELETE FROM api_keys;
      DELETE FROM team_invitations;
      DELETE FROM team_members;
      DELETE FROM teams;
      DELETE FROM users;
      INSERT INTO users (id, name, email) VALUES
        ('u-owner', 'Owner', 'owner@example.com'),
        ('u-admin', 'Admin', 'admin@example.com'),
        ('u-member', 'Member', 'member@example.com'),
        ('u-outsider', 'Outsider', 'outsider@example.com');
    `);
  });

  it("keeps a channel connected personally and in a team as two accounts", async () => {
    await db.exec(`INSERT INTO teams (id, name, slug, owner_id) VALUES ('t1', 'Alpha Crew', 'alpha-crew', 'u-owner')`);
    // What the previous release runs while it is still serving: its conflict target
    // needs the unique index on (user_id, provider, external_account_id) unchanged.
    const personalUpsert = `
      INSERT INTO social_accounts (id, user_id, provider, external_account_id, access_token_encrypted)
      VALUES ($1, 'u-owner', 'youtube', 'channel-1', $2)
      ON CONFLICT (user_id, provider, external_account_id)
      DO UPDATE SET access_token_encrypted = EXCLUDED.access_token_encrypted
      RETURNING id`;
    const teamUpsert = `
      INSERT INTO social_accounts (id, user_id, team_id, provider, external_account_id, access_token_encrypted)
      VALUES ($1, NULL, 't1', 'youtube', 'channel-1', $2)
      ON CONFLICT (team_id, provider, external_account_id) WHERE team_id IS NOT NULL
      DO UPDATE SET access_token_encrypted = EXCLUDED.access_token_encrypted
      RETURNING id`;

    const personal = (await db.query<{ id: string }>(personalUpsert, ["acc-personal", "personal-1"])).rows[0].id;
    const team = (await db.query<{ id: string }>(teamUpsert, ["acc-team", "team-1"])).rows[0].id;
    expect([personal, team]).toEqual(["acc-personal", "acc-team"]);

    // Reconnecting in one workspace never touches the other's row.
    expect((await db.query<{ id: string }>(personalUpsert, ["acc-other", "personal-2"])).rows[0].id).toBe("acc-personal");
    expect((await db.query<{ id: string }>(teamUpsert, ["acc-other", "team-2"])).rows[0].id).toBe("acc-team");
    const rows = (
      await db.query<{ id: string; user_id: string | null; team_id: string | null; access_token_encrypted: string }>(
        "SELECT id, user_id, team_id, access_token_encrypted FROM social_accounts ORDER BY id",
      )
    ).rows;
    expect(rows).toEqual([
      { id: "acc-personal", user_id: "u-owner", team_id: null, access_token_encrypted: "personal-2" },
      { id: "acc-team", user_id: null, team_id: "t1", access_token_encrypted: "team-2" },
    ]);

    // The team's account outlives the member who connected it and goes with the team.
    await db.exec("UPDATE teams SET owner_id = 'u-admin' WHERE id = 't1'; DELETE FROM users WHERE id = 'u-owner'");
    expect((await db.query("SELECT id FROM social_accounts")).rows).toEqual([{ id: "acc-team" }]);
    await db.exec("DELETE FROM teams WHERE id = 't1'");
    expect((await db.query("SELECT id FROM social_accounts")).rows).toEqual([]);
  });

  it("enforces unique slugs, unique memberships, unique token hashes, and Stripe IDs", async () => {
    await db.exec(`
      INSERT INTO teams (id, name, slug, owner_id, stripe_customer_id, stripe_subscription_id)
      VALUES ('t1', 'Alpha Crew', 'alpha-crew', 'u-owner', 'cus_team_alpha', 'sub_team_alpha');
    `);

    // Duplicate slug fails
    await expect(
      db.exec(`INSERT INTO teams (id, name, slug, owner_id) VALUES ('t2', 'Alpha 2', 'alpha-crew', 'u-owner')`),
    ).rejects.toThrow();

    // Duplicate Stripe customer fails
    await expect(
      db.exec(`INSERT INTO teams (id, name, slug, owner_id, stripe_customer_id) VALUES ('t2', 'Beta', 'beta', 'u-owner', 'cus_team_alpha')`),
    ).rejects.toThrow();

    // Duplicate (team_id, user_id) membership fails
    await db.exec(`INSERT INTO team_members (team_id, user_id, role) VALUES ('t1', 'u-owner', 'owner')`);
    await expect(
      db.exec(`INSERT INTO team_members (team_id, user_id, role) VALUES ('t1', 'u-owner', 'admin')`),
    ).rejects.toThrow();

    // Duplicate invitation token_hash fails
    await db.exec(`
      INSERT INTO team_invitations (id, team_id, email, role, token_hash, invited_by, expires_at)
      VALUES ('inv1', 't1', 'new@example.com', 'member', 'hash-unique-1', 'u-owner', NOW() + INTERVAL '7 days')
    `);
    await expect(
      db.exec(`
        INSERT INTO team_invitations (id, team_id, email, role, token_hash, invited_by, expires_at)
        VALUES ('inv2', 't1', 'other@example.com', 'member', 'hash-unique-1', 'u-owner', NOW() + INTERVAL '7 days')
      `),
    ).rejects.toThrow();
  });

  it("cascades memberships & invitations on team delete while nulling team_id on historical records", async () => {
    await db.exec(`
      INSERT INTO teams (id, name, slug, owner_id, plan, subscription_status, seat_limit)
      VALUES ('t1', 'Alpha', 'alpha', 'u-owner', 'team', 'active', 5);
      INSERT INTO team_members (team_id, user_id, role) VALUES
        ('t1', 'u-owner', 'owner'),
        ('t1', 'u-member', 'member');
      INSERT INTO team_invitations (id, team_id, email, role, token_hash, invited_by, expires_at)
      VALUES ('inv1', 't1', 'pending@example.com', 'member', 'hash-1', 'u-owner', NOW() + INTERVAL '7 days');
      INSERT INTO sources (id, url, title, type) VALUES ('s1', 'https://youtu.be/test1234567', 'Test', 'youtube');
      INSERT INTO tasks (id, user_id, team_id, source_id, status) VALUES ('task-1', 'u-member', 't1', 's1', 'completed');
      INSERT INTO minute_usage_events (id, user_id, team_id, task_id, minutes, source_duration_seconds)
      VALUES ('mu-1', 'u-member', 't1', 'task-1', 30, 1800);
      INSERT INTO ai_edit_usage_events (job_id, user_id, team_id)
      VALUES ('job-1', 'u-member', 't1');
    `);

    await db.exec("DELETE FROM teams WHERE id = 't1'");

    expect((await db.query("SELECT * FROM team_members")).rows).toHaveLength(0);
    expect((await db.query("SELECT * FROM team_invitations")).rows).toHaveLength(0);

    // tasks.team_id cascades on team delete; usage audit tables preserve their team_id so
    // deleted team history never leaks into a member's personal (team_id IS NULL) pool.
    const taskRow = (await db.query<{ team_id: string | null }>("SELECT team_id FROM tasks WHERE id = 'task-1'")).rows[0];
    expect(taskRow).toBeUndefined();
    const usageRow = (await db.query<{ team_id: string | null }>("SELECT team_id FROM minute_usage_events WHERE id = 'mu-1'")).rows[0];
    expect(usageRow?.team_id).toBe("t1");
    const aiRow = (await db.query<{ team_id: string | null }>("SELECT team_id FROM ai_edit_usage_events WHERE job_id = 'job-1'")).rows[0];
    expect(aiRow?.team_id).toBe("t1");
  });

  it("isolates team vs personal minute usage and refunds only clip-less terminal failures", async () => {
    await db.exec(`
      INSERT INTO teams (id, name, slug, owner_id, plan, subscription_status, seat_limit)
      VALUES ('t1', 'Alpha', 'alpha', 'u-owner', 'team', 'active', 4);
      INSERT INTO sources (id, url, title, type) VALUES ('s1', 'https://youtu.be/test1234567', 'Test', 'youtube');
      INSERT INTO tasks (id, user_id, team_id, source_id, status) VALUES
        ('t-ok', 'u-owner', 't1', 's1', 'completed'),
        ('t-fail-empty', 'u-member', 't1', 's1', 'error'),
        ('t-fail-with-clip', 'u-member', 't1', 's1', 'error'),
        ('t-personal', 'u-owner', NULL, 's1', 'completed');
      INSERT INTO generated_clips (id, task_id, filename, file_path, start_time, end_time, duration, relevance_score, clip_order) VALUES
        ('c1', 't-fail-with-clip', 'clip.mp4', '/tmp/clip.mp4', 0, 15, 15, 0.9, 1);
      INSERT INTO minute_usage_events (id, user_id, team_id, task_id, minutes, source_duration_seconds) VALUES
        ('m1', 'u-owner', 't1', 't-ok', 60, 3600),
        ('m2', 'u-member', 't1', 't-fail-empty', 45, 2700),
        ('m3', 'u-member', 't1', 't-fail-with-clip', 30, 1800),
        ('m4', 'u-owner', NULL, 't-personal', 20, 1200);
    `);

    // Run production refund query against both failed tasks.
    const refundSql = `
      UPDATE minute_usage_events SET refunded_at = CURRENT_TIMESTAMP
      WHERE task_id = $1 AND refunded_at IS NULL
        AND EXISTS (
          SELECT 1 FROM tasks t
          WHERE t.id = $1
            AND t.user_id = minute_usage_events.user_id
            AND (
              t.team_id = minute_usage_events.team_id
              OR (t.team_id IS NULL AND minute_usage_events.team_id IS NULL)
            )
            AND t.status IN ('error', 'failed', 'cancelled', 'canceled')
            AND NOT EXISTS (SELECT 1 FROM generated_clips c WHERE c.task_id = t.id)
        )
    `;
    await db.query(refundSql, ["t-fail-empty"]);
    await db.query(refundSql, ["t-fail-with-clip"]);

    // Deleting a completed task must NOT refund its minutes.
    await db.exec("DELETE FROM tasks WHERE id = 't-ok'");

    const teamTotal = (
      await db.query<{ minutes: string }>(
        "SELECT COALESCE(SUM(minutes), 0) AS minutes FROM minute_usage_events WHERE team_id = 't1' AND refunded_at IS NULL",
      )
    ).rows[0];
    // 60 (deleted completed task) + 30 (failed after clip saved) = 90; 45 was refunded.
    expect(Number(teamTotal?.minutes)).toBe(90);

    const personalTotal = (
      await db.query<{ minutes: string }>(
        "SELECT COALESCE(SUM(minutes), 0) AS minutes FROM minute_usage_events WHERE user_id = 'u-owner' AND team_id IS NULL AND refunded_at IS NULL",
      )
    ).rows[0];
    expect(Number(personalTotal?.minutes)).toBe(20);
  });
});
