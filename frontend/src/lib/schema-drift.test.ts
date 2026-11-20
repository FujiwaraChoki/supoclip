// @vitest-environment node
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const root = path.resolve(__dirname, "../../..");
const prismaSchema = readFileSync(path.join(root, "frontend/prisma/schema.prisma"), "utf8");
const initSql = readFileSync(path.join(root, "init.sql"), "utf8");
const repairSql = readFileSync(path.join(root, "scripts/selfhost-schema-repair.sql"), "utf8");

const SCALARS = new Set(["String", "Boolean", "Int", "BigInt", "Float", "Decimal", "DateTime", "Json", "Bytes"]);
const enums = new Set([...prismaSchema.matchAll(/^enum\s+(\w+)\s*\{/gm)].map((m) => m[1]));

function prismaColumns(): Map<string, string[]> {
  const tables = new Map<string, string[]>();
  for (const [, name, body] of prismaSchema.matchAll(/^model\s+(\w+)\s*\{([\s\S]*?)^\}/gm)) {
    const table = body.match(/@@map\("([^"]+)"\)/)?.[1] ?? name;
    const columns: string[] = [];
    for (const line of body.split("\n")) {
      const field = line.trim().match(/^(\w+)\s+(\w+)(\?|\[\])?(\s|$)/);
      if (!field || field[1].startsWith("@")) continue;
      const [, fieldName, type, modifier] = field;
      if (modifier === "[]" && !SCALARS.has(type) && !enums.has(type)) continue;
      if (!SCALARS.has(type) && !enums.has(type)) continue; // relation
      columns.push(line.match(/@map\("([^"]+)"\)/)?.[1] ?? fieldName);
    }
    tables.set(table, columns);
  }
  return tables;
}

function initColumns(): Map<string, Set<string>> {
  const tables = new Map<string, Set<string>>();
  for (const [, table, body] of initSql.matchAll(/CREATE TABLE (?:IF NOT EXISTS )?"?(\w+)"?\s*\(([\s\S]*?)\n\);/g)) {
    const columns = new Set<string>();
    for (const line of body.split("\n")) {
      const column = line.match(/^\s+"?(\w+)"?\s+[A-Za-z]/)?.[1];
      if (column && !/^(CONSTRAINT|PRIMARY|UNIQUE|FOREIGN|CHECK)$/i.test(column)) columns.add(column);
    }
    tables.set(table, columns);
  }
  for (const [, table, column] of initSql.matchAll(/ALTER TABLE "?(\w+)"?\s+ADD COLUMN (?:IF NOT EXISTS )?"?(\w+)"?/g)) {
    tables.get(table)?.add(column);
  }
  return tables;
}

describe("init.sql matches the Prisma schema", () => {
  // A fresh self-hosted install builds its database from init.sql alone, and Better Auth
  // reads every column Prisma knows about. One missing column makes sign-up return a 500.
  it("creates every table and column the Prisma models read", () => {
    const sql = initColumns();
    const missing: string[] = [];
    for (const [table, columns] of prismaColumns()) {
      const have = sql.get(table);
      if (!have) {
        missing.push(`${table} (table)`);
        continue;
      }
      for (const column of columns) if (!have.has(column)) missing.push(`${table}.${column}`);
    }
    expect(missing).toEqual([]);
  });

  it("ends with the catch-up script existing installs run, so the two cannot drift", () => {
    expect(initSql.trimEnd().endsWith(repairSql.trimEnd())).toBe(true);
  });

  it("keeps the catch-up script safe to run twice", () => {
    const statements = repairSql.split(";").map((s) => s.replace(/--.*$/gm, "").trim()).filter(Boolean);
    for (const statement of statements) {
      expect(statement).toMatch(/IF NOT EXISTS/);
    }
  });
});
