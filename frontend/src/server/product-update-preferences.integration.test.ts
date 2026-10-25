// @vitest-environment node
import { readFileSync } from "node:fs";
import { PGlite } from "@electric-sql/pglite";
import { expect, it } from "vitest";

it.each([false, true])("preserves product opt-outs when both migration runners execute (backend first: %s)", async (backendFirst) => {
  const db = new PGlite();
  try {
    await db.exec("CREATE TABLE users (id text PRIMARY KEY, notify_on_completion boolean NOT NULL DEFAULT true)");
    const migrations = [
      readFileSync("prisma/migrations/20261025160000_product_update_preferences/migration.sql", "utf8"),
      readFileSync("../backend/src/migrations/sql/20261025_0004_product_update_preferences.sql", "utf8"),
    ];
    if (backendFirst) migrations.reverse();
    await db.exec(migrations[0]);
    await db.exec("INSERT INTO users (id) VALUES ('recipient')");
    const defaults = await db.query<{ product_updates_unsubscribed_at: null }>("SELECT product_updates_unsubscribed_at FROM users");
    expect(defaults.rows[0].product_updates_unsubscribed_at).toBeNull();
    await db.exec("UPDATE users SET product_updates_unsubscribed_at='2026-10-25T12:00:00Z'");
    await db.exec(migrations[1]);
    const result = await db.query<{ product_updates_unsubscribed_at: Date; notify_on_completion: boolean }>("SELECT * FROM users");
    expect(result.rows[0].product_updates_unsubscribed_at.toISOString()).toBe("2026-10-25T12:00:00.000Z");
    expect(result.rows[0].notify_on_completion).toBe(true);
  } finally { await db.close(); }
}, 30_000);
