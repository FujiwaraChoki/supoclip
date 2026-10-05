import type { Attribution } from "@/lib/attribution";

/**
 * Writes to `user_acquisition`. Sign-up attribution and the first clip export
 * arrive from independent requests in either order, so each write is a single
 * upsert that only touches its own columns:
 *
 * - attribution fields are filled once (while `first_seen_at` is NULL), and
 * - `first_clip_exported_at` is set once (while it is NULL),
 *
 * which makes the result the same whichever request lands first.
 */

/** Only accounts this new are attributed, so later campaign clicks can't relabel existing users. */
export const ATTRIBUTION_SIGNUP_WINDOW_MS = 24 * 60 * 60 * 1000;

export type SqlExecutor = <T>(sql: string, params: unknown[]) => Promise<T[]>;

export type ExportAttribution = Pick<
  Attribution,
  "utm_source" | "utm_medium" | "utm_campaign" | "utm_content" | "ref" | "referrer_host"
>;

const ATTRIBUTION_COLUMNS = [
  "utm_source",
  "utm_medium",
  "utm_campaign",
  "utm_content",
  "utm_term",
  "ref",
  "referrer_host",
  "landing_path",
] as const;

const EXPORT_RETURNING = "utm_source, utm_medium, utm_campaign, utm_content, ref, referrer_host";

export function prismaSqlExecutor(prisma: { $queryRawUnsafe: (sql: string, ...params: unknown[]) => Promise<unknown> }): SqlExecutor {
  return async <T>(sql: string, params: unknown[]) => (await prisma.$queryRawUnsafe(sql, ...params)) as T[];
}

export function isWithinSignupWindow(createdAt: Date | string, now: Date) {
  const created = new Date(createdAt).getTime();
  return !Number.isNaN(created) && now.getTime() - created <= ATTRIBUTION_SIGNUP_WINDOW_MS;
}

/** Stores first-touch attribution unless it was already stored. Never touches the export timestamp. */
export async function saveSignupAttribution(sql: SqlExecutor, userId: string, attribution: Attribution) {
  const columns = ["user_id", ...ATTRIBUTION_COLUMNS, "first_seen_at"];
  const values = [userId, ...ATTRIBUTION_COLUMNS.map((column) => attribution[column] ?? null), new Date(attribution.captured_at)];
  const rows = await sql<{ user_id: string }>(
    `INSERT INTO user_acquisition AS ua (${columns.join(", ")})
     VALUES (${columns.map((_, index) => `$${index + 1}`).join(", ")})
     ON CONFLICT (user_id) DO UPDATE SET
       ${[...ATTRIBUTION_COLUMNS, "first_seen_at"].map((column) => `${column} = EXCLUDED.${column}`).join(", ")}
     WHERE ua.first_seen_at IS NULL
     RETURNING user_id`,
    values,
  );
  return rows.length > 0;
}

/**
 * Stamps the user's first clip export. Returns the stored attribution when this
 * call was the first export, or null for repeats and untracked users.
 * `allowInsert` lets a new account record its export before attribution syncs.
 */
export async function recordFirstClipExport(
  sql: SqlExecutor,
  userId: string,
  exportedAt: Date,
  { allowInsert }: { allowInsert: boolean },
): Promise<ExportAttribution | null> {
  const rows = allowInsert
    ? await sql<ExportAttribution>(
        `INSERT INTO user_acquisition AS ua (user_id, first_clip_exported_at)
         VALUES ($1, $2)
         ON CONFLICT (user_id) DO UPDATE SET first_clip_exported_at = EXCLUDED.first_clip_exported_at
         WHERE ua.first_clip_exported_at IS NULL
         RETURNING ${EXPORT_RETURNING}`,
        [userId, exportedAt],
      )
    : await sql<ExportAttribution>(
        `UPDATE user_acquisition SET first_clip_exported_at = $2
         WHERE user_id = $1 AND first_clip_exported_at IS NULL
         RETURNING ${EXPORT_RETURNING}`,
        [userId, exportedAt],
      );
  return rows[0] ?? null;
}

/**
 * The account's frozen sign-up attribution, or null when none was stored (for
 * example an export-only row, or an account that predates tracking).
 */
export async function getSignupAttribution(sql: SqlExecutor, userId: string): Promise<Attribution | null> {
  const [row] = await sql<Record<string, string | Date | null>>(
    `SELECT ${ATTRIBUTION_COLUMNS.join(", ")}, first_seen_at
     FROM user_acquisition
     WHERE user_id = $1 AND first_seen_at IS NOT NULL`,
    [userId],
  );
  if (!row) return null;
  const attribution: Attribution = { captured_at: new Date(row.first_seen_at as Date | string).toISOString() };
  for (const column of ATTRIBUTION_COLUMNS) {
    const value = row[column];
    if (typeof value === "string" && value) attribution[column] = value;
  }
  return attribution;
}
