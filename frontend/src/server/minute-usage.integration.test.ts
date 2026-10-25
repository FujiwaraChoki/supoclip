// @vitest-environment node
import { readFileSync } from "node:fs";
import { PGlite } from "@electric-sql/pglite";
import { expect, it } from "vitest";

const backend = readFileSync("../backend/src/migrations/sql/20261025_0002_minute_usage_ledger.sql", "utf8");
const frontend = readFileSync("prisma/migrations/20261025120000_add_minute_usage_ledger/migration.sql", "utf8");
const fixedNow = "TIMESTAMPTZ '2026-03-30 12:00:00+00'";

it("backfills matching UTC monthly windows, preserves charges on deletion, and runs idempotently in either runner", async () => {
  expect(frontend.split("-- The backend migration")[0]).toBe(backend.split("-- Preserve current minute totals")[0]);
  const db = new PGlite();
  try {
    await db.exec(`
      SET TIME ZONE 'Europe/Zurich';
      CREATE TABLE users (id TEXT PRIMARY KEY, billing_period_start TIMESTAMPTZ, billing_period_end TIMESTAMPTZ);
      CREATE TABLE tasks (id TEXT PRIMARY KEY, user_id TEXT, status TEXT, source_duration_seconds INTEGER, created_at TIMESTAMPTZ);
      CREATE TABLE generated_clips (id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE);
      INSERT INTO users VALUES
        ('annual', '2026-01-31 15:00+00', '2027-01-31 15:00+00'),
        ('monthly', '2026-03-12 10:00+00', '2026-04-12 10:00+00'),
        ('calendar', NULL, NULL), ('half-period', NULL, '2026-04-12 10:00+00');
      INSERT INTO tasks VALUES
        ('annual-before', 'annual', 'completed', 60, '2026-02-28 14:59:59+00'),
        ('annual-start', 'annual', 'completed', 61, '2026-02-28 15:00+00'),
        ('annual-end', 'annual', 'queued', 120, '2026-03-31 15:00+00'),
        ('annual-after', 'annual', 'queued', 120, '2026-03-31 15:00:01+00'),
        ('monthly-before', 'monthly', 'completed', 60, '2026-03-12 09:59:59+00'),
        ('monthly-start', 'monthly', 'completed', 1, '2026-03-12 10:00+00'),
        ('monthly-end', 'monthly', 'queued', 60, '2026-04-12 10:00+00'),
        ('calendar-start', 'calendar', 'processing', 121, '2026-03-01 00:00+00'),
        ('calendar-end', 'calendar', 'completed', 60, '2026-03-31 23:59:59.999999+00'),
        ('calendar-after', 'calendar', 'completed', 60, '2026-04-01 00:00+00'),
        ('partial', 'calendar', 'error', 61, '2026-03-20 00:00+00'),
        ('failed', 'calendar', 'error', 600, '2026-03-20 00:00+00'),
        ('cancelled', 'calendar', 'cancelled', 600, '2026-03-20 00:00+00'),
        ('unknown', 'calendar', 'completed', NULL, '2026-03-20 00:00+00'),
        ('zero', 'calendar', 'completed', 0, '2026-03-20 00:00+00'),
        ('half-start', 'half-period', 'completed', 60, '2026-03-01 00:00+00');
      INSERT INTO generated_clips VALUES ('clip-1', 'partial'), ('clip-2', 'partial');
    `);
    // Follow the backend runner's exact semicolon-splitting behavior too.
    for (const statement of backend.replaceAll("CURRENT_TIMESTAMP", fixedNow).split(";")) {
      if (statement.trim()) await db.exec(statement);
    }
    await db.exec(frontend.replaceAll("CURRENT_TIMESTAMP", fixedNow));
    const rows = await db.query<{ task_id: string; minutes: number }>(
      "SELECT task_id, minutes FROM minute_usage_events ORDER BY task_id",
    );
    expect(rows.rows).toEqual([
      { task_id: "annual-end", minutes: 2 },
      { task_id: "annual-start", minutes: 2 },
      { task_id: "calendar-end", minutes: 1 },
      { task_id: "calendar-start", minutes: 3 },
      { task_id: "half-start", minutes: 1 },
      { task_id: "monthly-end", minutes: 1 },
      { task_id: "monthly-start", minutes: 1 },
      { task_id: "partial", minutes: 2 },
    ]);
    const timestamp = await db.query<{ matches: boolean }>(`
      SELECT e.created_at = t.created_at AS matches
      FROM minute_usage_events e JOIN tasks t ON t.id = e.task_id
    `);
    expect(timestamp.rows.every((row) => row.matches)).toBe(true);
    const refundColumn = await db.query<{ is_nullable: string; data_type: string }>(`
      SELECT is_nullable, data_type FROM information_schema.columns
      WHERE table_name = 'minute_usage_events' AND column_name = 'refunded_at'
    `);
    expect(refundColumn.rows).toEqual([{ is_nullable: "YES", data_type: "timestamp with time zone" }]);
    await db.exec("UPDATE minute_usage_events SET refunded_at = CURRENT_TIMESTAMP WHERE task_id = 'annual-start'");
    // Replaying the backfill must preserve a later refund, never reactivate it.
    await db.exec(backend.replaceAll("CURRENT_TIMESTAMP", fixedNow));
    const usage = await db.query("SELECT SUM(minutes) AS minutes FROM minute_usage_events WHERE refunded_at IS NULL");
    expect(usage.rows).toEqual([{ minutes: 11 }]);
    await db.exec("DELETE FROM generated_clips; DELETE FROM tasks; DELETE FROM users;");
    expect((await db.query("SELECT task_id, minutes FROM minute_usage_events ORDER BY task_id")).rows).toEqual(rows.rows);
    expect((await db.query("SELECT SUM(minutes) AS minutes FROM minute_usage_events WHERE refunded_at IS NULL")).rows)
      .toEqual(usage.rows);
    expect((await db.query("SELECT task_id FROM minute_usage_events WHERE refunded_at IS NOT NULL")).rows)
      .toEqual([{ task_id: "annual-start" }]);
    await expect(db.exec(`INSERT INTO minute_usage_events (id,user_id,task_id,minutes,source_duration_seconds)
      VALUES ('duplicate','annual','annual-start',2,61)`)).rejects.toThrow(/unique/i);
    await expect(db.exec(`INSERT INTO minute_usage_events (id,user_id,task_id,minutes,source_duration_seconds)
      VALUES ('invalid','annual','invalid',1,61)`)).rejects.toThrow(/check/i);
  } finally {
    await db.close();
  }
}, 30_000);

it("anchors annual windows to the original day across clamped and future anniversaries", async () => {
  const db = new PGlite();
  try {
    await db.exec(`
      CREATE TABLE users (id TEXT PRIMARY KEY, billing_period_start TIMESTAMPTZ, billing_period_end TIMESTAMPTZ);
      CREATE TABLE tasks (id TEXT PRIMARY KEY, user_id TEXT, status TEXT, source_duration_seconds INTEGER, created_at TIMESTAMPTZ);
      CREATE TABLE generated_clips (task_id TEXT);
      INSERT INTO users VALUES ('annual','2026-01-31 00:00+00','2027-01-31 00:00+00');
      INSERT INTO tasks VALUES
        ('february','annual','completed',60,'2026-02-28 00:00+00'),
        ('march','annual','completed',60,'2026-03-31 00:00+00'),
        ('april','annual','completed',60,'2026-04-30 00:00+00');
    `);
    await db.exec(backend.replaceAll("CURRENT_TIMESTAMP", "TIMESTAMPTZ '2026-04-29 12:00+00'"));
    expect((await db.query<{ task_id: string }>("SELECT task_id FROM minute_usage_events ORDER BY task_id")).rows)
      .toEqual([{ task_id: "april" }, { task_id: "march" }]);
  } finally {
    await db.close();
  }
}, 30_000);

it("allows Prisma to create the ledger before the backend creates generated_clips and backfills", async () => {
  const db = new PGlite();
  try {
    await db.exec(`
      CREATE TABLE users (id TEXT PRIMARY KEY, billing_period_start TIMESTAMPTZ, billing_period_end TIMESTAMPTZ);
      CREATE TABLE tasks (id TEXT PRIMARY KEY, user_id TEXT, status TEXT, source_duration_seconds INTEGER, created_at TIMESTAMPTZ);
      INSERT INTO users VALUES ('monthly', '2026-03-01', '2026-04-01');
      INSERT INTO tasks VALUES ('task', 'monthly', 'completed', 61, '2026-03-15');
    `);
    await db.exec(frontend);
    await db.exec("CREATE TABLE generated_clips (task_id TEXT)");
    for (const statement of backend.replaceAll("CURRENT_TIMESTAMP", fixedNow).split(";")) {
      if (statement.trim()) await db.exec(statement);
    }
    expect((await db.query("SELECT task_id, minutes FROM minute_usage_events")).rows)
      .toEqual([{ task_id: "task", minutes: 2 }]);
  } finally {
    await db.close();
  }
}, 30_000);
