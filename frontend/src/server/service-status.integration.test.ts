// @vitest-environment node
import { readFileSync } from "node:fs";
import { PGlite } from "@electric-sql/pglite";

const migration = readFileSync(new URL("../../prisma/migrations/20261013120000_add_service_statuses/migration.sql", import.meta.url), "utf8");

describe("service status migration", () => {
  let db: PGlite;
  beforeAll(async () => { db = new PGlite(); await db.exec(migration); });
  afterAll(async () => { await db.close(); });

  it("seeds degraded downloads and preserves an operator's resolution when reapplied", async () => {
    expect((await db.query("SELECT id, status FROM service_statuses")).rows).toEqual([{ id: "video-downloads", status: "degraded" }]);
    await db.exec("UPDATE service_statuses SET status = 'operational', headline = 'Resolved', description = 'Downloads are working normally.', updated_at = CURRENT_TIMESTAMP WHERE id = 'video-downloads'");
    await db.exec(migration);
    expect((await db.query("SELECT status, headline FROM service_statuses")).rows).toEqual([{ status: "operational", headline: "Resolved" }]);
  });

  it("rejects unsupported status values", async () => {
    await expect(db.exec("UPDATE service_statuses SET status = 'typo' WHERE id = 'video-downloads'")).rejects.toThrow();
  });
});
