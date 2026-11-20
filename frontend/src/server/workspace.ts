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

/** Verify the cookie's team belongs to `userId`; returns null if not a member. */
export async function getVerifiedActiveTeamId(userId: string): Promise<string | null> {
  const teamId = await getActiveTeamIdFromCookies();
  if (!teamId) return null;
  try {
    const membership = await getPrismaClient().teamMember.findUnique({
      where: { team_id_user_id: { team_id: teamId, user_id: userId } },
      select: { team_id: true },
    });
    return membership?.team_id ?? null;
  } catch {
    return null;
  }
}
