// @vitest-environment node
import { readFileSync } from "node:fs";
import { PGlite } from "@electric-sql/pglite";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const prismaCreate = read("../../prisma/migrations/20261013120000_add_service_statuses/migration.sql");
const prismaReset = read("../../prisma/migrations/20261120120000_service_status_default_operational/migration.sql");
const backendCreate = read("../../../backend/src/migrations/sql/20261013_0002_service_statuses.sql");
const backendReset = read("../../../backend/src/migrations/sql/20261120_0001_service_status_default_operational.sql");

const currentStatus = async (db: PGlite) =>
  (await db.query("SELECT status, headline FROM service_statuses WHERE id = 'video-downloads'")).rows;

describe("service status migration", () => {
  let db: PGlite;
  beforeAll(async () => { db = await PGlite.create(); }, 30_000);
  afterAll(async () => { await db.close(); });
  beforeEach(async () => { await db.exec("DROP TABLE IF EXISTS service_statuses"); });

  it("starts a fresh self-hosted install as operational", async () => {
    await db.exec(backendCreate);
    expect(await currentStatus(db)).toEqual([{ status: "operational", headline: "Operational" }]);
    await db.exec(backendReset);
    expect(await currentStatus(db)).toEqual([{ status: "operational", headline: "Operational" }]);
  });

  it("resets the untouched October incident seed on databases that already have it", async () => {
    await db.exec(prismaCreate);
    expect(await currentStatus(db)).toEqual([{ status: "degraded", headline: "Investigating" }]);
    await db.exec(prismaReset);
    expect(await currentStatus(db)).toEqual([{ status: "operational", headline: "Operational" }]);
    expect(prismaReset).toBe(backendReset);
  });

  it("keeps a real incident an operator has posted", async () => {
    await db.exec(prismaCreate);
    await db.exec("UPDATE service_statuses SET headline = 'Investigating', description = 'YouTube imports are failing.', updated_at = CURRENT_TIMESTAMP WHERE id = 'video-downloads'");
    await db.exec(backendReset);
    expect(await currentStatus(db)).toEqual([{ status: "degraded", headline: "Investigating" }]);
  });

  it("preserves an operator's resolution when reapplied", async () => {
    await db.exec(backendCreate);
    await db.exec("UPDATE service_statuses SET status = 'outage', headline = 'Down', description = 'Downloads are failing.', updated_at = CURRENT_TIMESTAMP WHERE id = 'video-downloads'");
    await db.exec(backendCreate);
    await db.exec(backendReset);
    expect(await currentStatus(db)).toEqual([{ status: "outage", headline: "Down" }]);
  });

  it("rejects unsupported status values", async () => {
    await db.exec(backendCreate);
    await expect(db.exec("UPDATE service_statuses SET status = 'typo' WHERE id = 'video-downloads'")).rejects.toThrow();
  });
});
