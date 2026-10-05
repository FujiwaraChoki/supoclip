// @vitest-environment node
import { readFileSync } from "node:fs";

import { PGlite } from "@electric-sql/pglite";
import { uuid_ossp } from "@electric-sql/pglite/contrib/uuid_ossp";

import type { Attribution } from "@/lib/attribution";
import { getSignupAttribution, recordFirstClipExport, saveSignupAttribution, type SqlExecutor } from "./user-acquisition";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

const attribution: Attribution = {
  utm_source: "reddit",
  utm_campaign: "launch_2026_10",
  landing_path: "/demo",
  captured_at: "2026-10-05T10:00:00.000Z",
};
const exportedAt = new Date("2026-10-05T12:00:00.000Z");

describe("user_acquisition writes (real Postgres)", () => {
  let db: PGlite;
  let sql: SqlExecutor;

  beforeAll(async () => {
    db = new PGlite({ extensions: { uuid_ossp } });
    await db.exec(read("../../../init.sql"));
    // Production databases get the table from the Prisma migration, so test that DDL.
    await db.exec("DROP TABLE user_acquisition");
    await db.exec(read("../../prisma/migrations/20261005120000_add_user_acquisition/migration.sql"));
    sql = async <T>(query: string, params: unknown[]) => (await db.query<T>(query, params)).rows;
  });

  afterAll(async () => {
    await db.close();
  });

  beforeEach(async () => {
    await db.exec(`DELETE FROM users; INSERT INTO users (id, name, email) VALUES ('u1', 'Test', 'u1@example.com');`);
  });

  const row = async () =>
    (await db.query<Record<string, unknown>>("SELECT * FROM user_acquisition WHERE user_id = 'u1'")).rows[0];

  it("keeps a first export that lands before the attribution sync", async () => {
    await expect(recordFirstClipExport(sql, "u1", exportedAt, { allowInsert: true })).resolves.not.toBeNull();

    await expect(saveSignupAttribution(sql, "u1", attribution)).resolves.toBe(true);

    expect(await row()).toMatchObject({
      utm_source: "reddit",
      utm_campaign: "launch_2026_10",
      landing_path: "/demo",
      first_seen_at: new Date(attribution.captured_at),
      first_clip_exported_at: exportedAt,
    });
    // The early export already counted; later exports must not count again.
    await expect(recordFirstClipExport(sql, "u1", new Date(), { allowInsert: true })).resolves.toBeNull();
    expect((await row()).first_clip_exported_at).toEqual(exportedAt);
  });

  it("records the first export after sync once and returns the stored attribution", async () => {
    await saveSignupAttribution(sql, "u1", attribution);

    await expect(recordFirstClipExport(sql, "u1", exportedAt, { allowInsert: true })).resolves.toEqual({
      utm_source: "reddit",
      utm_medium: null,
      utm_campaign: "launch_2026_10",
      utm_content: null,
      ref: null,
      referrer_host: null,
    });
    await expect(recordFirstClipExport(sql, "u1", new Date(), { allowInsert: true })).resolves.toBeNull();
    expect((await row()).first_clip_exported_at).toEqual(exportedAt);
  });

  it("counts exactly one first export when two exports race", async () => {
    const results = await Promise.all([
      recordFirstClipExport(sql, "u1", exportedAt, { allowInsert: true }),
      recordFirstClipExport(sql, "u1", new Date("2026-10-05T12:00:01.000Z"), { allowInsert: true }),
    ]);

    expect(results.filter(Boolean)).toHaveLength(1);
  });

  it("keeps the first attribution when a second sync arrives", async () => {
    await saveSignupAttribution(sql, "u1", attribution);

    await expect(
      saveSignupAttribution(sql, "u1", { utm_source: "newsletter", captured_at: "2026-10-05T11:00:00.000Z" }),
    ).resolves.toBe(false);
    expect((await row()).utm_source).toBe("reddit");
  });

  it("does not start tracking accounts that predate attribution", async () => {
    await expect(recordFirstClipExport(sql, "u1", exportedAt, { allowInsert: false })).resolves.toBeNull();
    expect(await row()).toBeUndefined();
  });

  it("reads back the frozen attribution, ignoring export-only rows and later syncs", async () => {
    await recordFirstClipExport(sql, "u1", exportedAt, { allowInsert: true });
    await expect(getSignupAttribution(sql, "u1")).resolves.toBeNull();

    await saveSignupAttribution(sql, "u1", attribution);
    await saveSignupAttribution(sql, "u1", { utm_source: "newsletter", captured_at: "2026-10-05T11:00:00.000Z" });

    await expect(getSignupAttribution(sql, "u1")).resolves.toEqual(attribution);
  });

  it("keeps each account's attribution separate", async () => {
    await db.exec(`INSERT INTO users (id, name, email) VALUES ('u2', 'Other', 'u2@example.com');`);
    await saveSignupAttribution(sql, "u1", attribution);

    await expect(getSignupAttribution(sql, "u2")).resolves.toBeNull();
  });
});
