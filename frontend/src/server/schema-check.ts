import { Prisma } from "@/generated/prisma";
import prisma from "@/lib/prisma";

const REPAIR_COMMAND =
  "docker compose exec -T postgres psql -U supoclip -d supoclip < scripts/selfhost-schema-repair.sql";

type ModelColumns = { table: string; columns: string[] };

/** Tables and columns the Prisma client selects, as the database names them. */
export function expectedColumns(): ModelColumns[] {
  return Prisma.dmmf.datamodel.models.map((model) => ({
    table: model.dbName ?? model.name,
    columns: model.fields
      .filter((field) => field.kind === "scalar" || field.kind === "enum")
      .map((field) => field.dbName ?? field.name),
  }));
}

/** `table` for a missing table, `table.column` for a missing column. */
export function findMissingColumns(
  expected: ModelColumns[],
  present: { table_name: string; column_name: string }[],
): string[] {
  const have = new Map<string, Set<string>>();
  for (const { table_name, column_name } of present) {
    if (!have.has(table_name)) have.set(table_name, new Set());
    have.get(table_name)!.add(column_name);
  }
  const missing: string[] = [];
  for (const { table, columns } of expected) {
    const found = have.get(table);
    if (!found) {
      missing.push(table);
      continue;
    }
    for (const column of columns) if (!found.has(column)) missing.push(`${table}.${column}`);
  }
  return missing;
}

/**
 * Sign-up, sign-in and every page read the whole `users` row through Prisma, so a database
 * that lags the Prisma schema fails with an opaque 500. Say so once, at boot, with the fix.
 * Never throws: a warning must not stop the server.
 */
export async function warnIfSchemaOutOfDate(): Promise<void> {
  try {
    const present = await prisma.$queryRaw<{ table_name: string; column_name: string }[]>`
      SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = current_schema()`;
    const missing = findMissingColumns(expectedColumns(), present);
    if (missing.length === 0) return;
    console.error(
      `[SupoClip] The database is missing ${missing.length} table(s)/column(s) the app expects: ` +
        `${missing.slice(0, 12).join(", ")}${missing.length > 12 ? ", ..." : ""}. ` +
        `Sign-up and sign-in will fail with a 500 until this is fixed. ` +
        `From the repository folder run: ${REPAIR_COMMAND}`,
    );
  } catch (error) {
    console.warn("[SupoClip] Could not check the database schema at startup", error);
  }
}
