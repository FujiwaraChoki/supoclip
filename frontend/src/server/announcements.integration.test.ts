// @vitest-environment node
import { readFileSync } from "node:fs";
import { PGlite } from "@electric-sql/pglite";

describe("announcements migration (real Postgres)", () => {
  let db: PGlite;
  const migration = readFileSync(new URL("../../prisma/migrations/20261011140000_add_announcements/migration.sql", import.meta.url), "utf8");
  beforeAll(async () => { db = new PGlite(); await db.exec(migration); });
  afterAll(async () => { await db.close(); });

  it("seeds the existing creator announcement without resetting its identity or overwriting edits on reapplication", async () => {
    expect((await db.query("SELECT id, is_published FROM announcements")).rows).toEqual([{ id: "creator-program-2026-10", is_published: true }]);
    await db.exec("UPDATE announcements SET title = 'Edited in admin' WHERE id = 'creator-program-2026-10'");
    await db.exec(migration);
    expect((await db.query("SELECT title FROM announcements")).rows).toEqual([{ title: "Edited in admin" }]);
  });

  it("keeps drafts and scheduled posts out of the published feed until their publication time", async () => {
    await db.exec(`INSERT INTO announcements (id, title, description, href, action, is_published, published_at) VALUES
      ('draft', 'Draft', 'Hidden', '/settings', 'Read', false, '2026-01-01'),
      ('scheduled', 'Scheduled', 'Later', '/settings', 'Read', true, '2099-01-01');`);
    expect((await db.query("SELECT id FROM announcements WHERE is_published = true AND published_at <= '2026-10-11' ORDER BY published_at DESC")).rows)
      .toEqual([{ id: "creator-program-2026-10" }]);
    await db.exec("UPDATE announcements SET is_published = false WHERE id = 'creator-program-2026-10'");
    expect((await db.query("SELECT id FROM announcements WHERE is_published = true AND published_at <= '2026-10-11'")).rows).toEqual([]);
  });
});
