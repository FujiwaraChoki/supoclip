// @vitest-environment node
import { readFileSync } from "node:fs";

import { PGlite } from "@electric-sql/pglite";
import { uuid_ossp } from "@electric-sql/pglite/contrib/uuid_ossp";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

describe("affiliates table (real Postgres)", () => {
  let db: PGlite;

  beforeAll(async () => {
    db = new PGlite({ extensions: { uuid_ossp } });
    await db.exec(read("../../../init.sql"));
    // Production databases get the table from the Prisma migration, so test that DDL.
    await db.exec("DROP TABLE affiliates");
    await db.exec(read("../../prisma/migrations/20261009120000_add_affiliates/migration.sql"));
  });

  afterAll(async () => {
    await db.close();
  });

  beforeEach(async () => {
    await db.exec(`
      DELETE FROM affiliates;
      DELETE FROM users;
      INSERT INTO users (id, name, email) VALUES ('u1', 'One', 'u1@example.com'), ('u2', 'Two', 'u2@example.com'), ('u3', 'Three', 'u3@example.com');
    `);
  });

  const apply = (id: string, userId: string, slug: string | null) =>
    db.query(
      `INSERT INTO affiliates (id, user_id, slug, platform, profile_url, audience_size, terms_accepted_at)
       VALUES ($1, $2, $3, 'tiktok', 'https://tiktok.com/@x', '1k-10k', now())`,
      [id, userId, slug],
    );

  it("lets only one applicant hold a slug", async () => {
    await apply("a1", "u1", "maya");
    await expect(apply("a2", "u2", "maya")).rejects.toThrow(/affiliates_slug_key/);
  });

  it("frees slugs cleared on decline for several users", async () => {
    await apply("a1", "u1", null);
    await apply("a2", "u2", null);
    await expect(apply("a3", "u3", "maya")).resolves.toBeDefined();
  });

  it("keeps one application per user and removes it with the account", async () => {
    await apply("a1", "u1", "maya");
    await expect(apply("a2", "u1", "maya2")).rejects.toThrow(/affiliates_user_id_key/);

    await db.exec("DELETE FROM users WHERE id = 'u1'");
    expect((await db.query("SELECT * FROM affiliates")).rows).toHaveLength(0);
  });
});
