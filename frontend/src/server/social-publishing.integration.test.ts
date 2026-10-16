// @vitest-environment node
import { readFileSync } from "node:fs";
import { PGlite } from "@electric-sql/pglite";
import { uuid_ossp } from "@electric-sql/pglite/contrib/uuid_ossp";
import { expect, it } from "vitest";

const frontend = readFileSync("prisma/migrations/20261016130000_add_social_publishing/migration.sql", "utf8");
const backend = readFileSync("../backend/src/migrations/sql/20260901_0001_social_publishing.sql", "utf8");
const TABLES = ["social_accounts", "social_oauth_states", "social_posts", "social_post_metrics"];

// The tables the social migration references, as both runners create them.
const PREREQUISITES = `
  CREATE TABLE users (id TEXT PRIMARY KEY);
  CREATE TABLE tasks (id TEXT PRIMARY KEY);
  CREATE TABLE generated_clips (id VARCHAR(36) PRIMARY KEY);
`;

async function schemaAfter(...migrations: string[]) {
  const db = new PGlite({ extensions: { uuid_ossp } });
  try {
    await db.exec(PREREQUISITES);
    for (const migration of migrations) await db.exec(migration);
    const columns = await db.query<{ table_name: string; column_name: string; data_type: string; is_nullable: string }>(
      `SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns
       WHERE table_name = ANY($1) ORDER BY table_name, column_name`,
      [TABLES],
    );
    const indexes = await db.query<{ indexname: string }>(
      "SELECT indexname FROM pg_indexes WHERE tablename = ANY($1) ORDER BY indexname",
      [TABLES],
    );
    return { columns: columns.rows, indexes: indexes.rows.map((row) => row.indexname) };
  } finally {
    await db.close();
  }
}

it("creates identical social tables from either migration runner, in either order", async () => {
  const frontendFirst = await schemaAfter(frontend, backend);
  const backendFirst = await schemaAfter(backend, frontend);
  expect(frontendFirst).toEqual(backendFirst);
  expect(new Set(frontendFirst.columns.map((column) => column.table_name))).toEqual(new Set(TABLES));
  // The publish claim and stale-publishing sweep depend on claimed_at.
  expect(frontendFirst.columns).toContainEqual({
    table_name: "social_posts", column_name: "claimed_at",
    data_type: "timestamp with time zone", is_nullable: "YES",
  });
  expect(frontendFirst.indexes).toContain("idx_social_posts_status_scheduled");
}, 30_000);

it("applies Prisma's copy before the backend has created generated_clips", async () => {
  const db = new PGlite({ extensions: { uuid_ossp } });
  try {
    await db.exec(`
      CREATE TABLE users (id TEXT PRIMARY KEY);
      CREATE TABLE tasks (id TEXT PRIMARY KEY);
    `);
    await db.exec(frontend);
    await db.exec("CREATE TABLE generated_clips (id VARCHAR(36) PRIMARY KEY)");
    await db.exec(backend);
    const constraints = await db.query<{ conname: string }>(
      "SELECT conname FROM pg_constraint WHERE conname = 'social_posts_clip_id_fkey'",
    );
    expect(constraints.rows).toHaveLength(1);
  } finally {
    await db.close();
  }
}, 30_000);

it("adds the clip foreign key whichever runner goes first", async () => {
  for (const order of [[frontend, backend], [backend, frontend]]) {
    const db = new PGlite({ extensions: { uuid_ossp } });
    try {
      await db.exec(PREREQUISITES);
      for (const migration of order) await db.exec(migration);
      const constraints = await db.query<{ conname: string }>(
        "SELECT conname FROM pg_constraint WHERE conname = 'social_posts_clip_id_fkey'",
      );
      expect(constraints.rows).toHaveLength(1);
    } finally {
      await db.close();
    }
  }
}, 30_000);

it("cascades social rows with their user and keeps posts when a clip is deleted", async () => {
  const db = new PGlite({ extensions: { uuid_ossp } });
  try {
    await db.exec(PREREQUISITES);
    await db.exec(frontend);
    await db.exec(backend);
    await db.exec(`
      INSERT INTO users (id) VALUES ('user-1');
      INSERT INTO generated_clips (id) VALUES ('clip-1');
      INSERT INTO social_accounts (id, user_id, provider, external_account_id, access_token_encrypted)
        VALUES ('acc-1', 'user-1', 'youtube', 'chan-1', 'v1:encrypted');
      INSERT INTO social_posts (id, user_id, clip_id, social_account_id, provider)
        VALUES ('post-1', 'user-1', 'clip-1', 'acc-1', 'youtube');
      DELETE FROM generated_clips WHERE id = 'clip-1';
    `);
    const post = await db.query<{ clip_id: string | null; hashtags: string[] }>(
      "SELECT clip_id, hashtags FROM social_posts WHERE id = 'post-1'",
    );
    expect(post.rows).toEqual([{ clip_id: null, hashtags: [] }]);
    await db.exec("DELETE FROM users WHERE id = 'user-1'");
    for (const table of TABLES) {
      expect((await db.query(`SELECT 1 FROM ${table}`)).rows).toHaveLength(0);
    }
  } finally {
    await db.close();
  }
}, 30_000);
