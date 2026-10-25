// @vitest-environment node
import { readFileSync } from "node:fs";
import { PGlite } from "@electric-sql/pglite";
import { expect, it } from "vitest";

it("preserves clip length defaults in either migration order and rejects invalid stored presets", async () => {
  const frontend = readFileSync("prisma/migrations/20261025160000_add_default_clip_length/migration.sql", "utf8");
  const backend = readFileSync("../backend/src/migrations/sql/20261025_0004_default_clip_length.sql", "utf8");
  for (const migrations of [[frontend, backend], [backend, frontend]]) {
    const db = new PGlite();
    try {
      await db.exec("CREATE TABLE users (id text PRIMARY KEY); INSERT INTO users VALUES ('user-1');");
      await db.exec(migrations[0]);
      expect((await db.query("SELECT default_clip_length FROM users")).rows).toEqual([{ default_clip_length: "auto" }]);
      await db.exec("UPDATE users SET default_clip_length='long'");
      await db.exec(migrations[1]);
      expect((await db.query("SELECT default_clip_length FROM users")).rows).toEqual([{ default_clip_length: "long" }]);
      await expect(db.exec("UPDATE users SET default_clip_length='unknown'")).rejects.toThrow(/check constraint/);
      await expect(db.exec("UPDATE users SET default_clip_length=NULL")).rejects.toThrow(/not-null constraint/);
    } finally {
      await db.close();
    }
  }
}, 30_000);
