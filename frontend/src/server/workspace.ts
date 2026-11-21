import { getPrismaClient } from "@/server/prisma";

export const WORKSPACE_COOKIE = "supoclip_team_id";
const TEAM_ID_PATTERN = /^[A-Za-z0-9_-]{8,64}$/;

/** Read the active team id from the request cookie store (or null for personal workspace). */
export async function getActiveTeamIdFromCookies(): Promise<string | null> {
  try {
    const { cookies } = await import("next/headers");
    const store = await cookies();
    const raw = store.get(WORKSPACE_COOKIE)?.value?.trim();
    if (!raw || !TEAM_ID_PATTERN.test(raw)) return null;
    return raw;
  } catch {
    // Outside a Next.js request scope (scripts, unit tests).
    return null;
  }
}

/**
 * The cookie's team when `userId` still belongs to it, otherwise null (personal).
 *
 * The cookie outlives a membership: a removed member, a deleted team or another
 * person signing in on the same browser all leave it behind. Sending that id to
 * the backend makes every request fail with "Not a member of this team", so a
 * stale cookie reads as the personal workspace. A database error is thrown, not
 * treated as "no team": a task meant for a team must never be billed to the
 * person because a lookup failed.
 */
export async function getVerifiedActiveTeamId(userId: string): Promise<string | null> {
  const teamId = await getActiveTeamIdFromCookies();
  if (!teamId) return null;
  const membership = await getPrismaClient().teamMember.findUnique({
    where: { team_id_user_id: { team_id: teamId, user_id: userId } },
    select: { team_id: true },
  });
  return membership?.team_id ?? null;
}
