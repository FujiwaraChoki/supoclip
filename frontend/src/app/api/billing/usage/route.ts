import { NextResponse } from "next/server";

import type { BillingSummary } from "@/hooks/use-billing-summary";
import type { WorkspaceUsage } from "@/lib/workspace-usage";
import { fetchBackend } from "@/server/backend-api";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";
import { getVerifiedActiveTeamId } from "@/server/workspace";

// The account menu lists these side by side; nobody needs more on one screen.
const MAX_TEAMS = 10;

/**
 * This month's allowance in every workspace the caller belongs to: personal
 * first, then teams in the order they were joined. Each summary comes from the
 * backend with that workspace's signed team header, so the backend still checks
 * membership. A workspace whose summary fails is left out instead of failing all.
 */
export async function GET() {
  const session = await getServerSession();
  const userId = session?.user?.id;
  if (!userId) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const [memberships, activeTeamId] = await Promise.all([
    getPrismaClient().teamMember.findMany({
      where: { user_id: userId },
      select: { role: true, team: { select: { id: true, name: true } } },
      orderBy: { created_at: "asc" },
      take: MAX_TEAMS,
    }),
    getVerifiedActiveTeamId(userId),
  ]);

  const targets: Omit<WorkspaceUsage, "summary">[] = [
    { teamId: null, name: null, role: null },
    ...memberships.map((membership) => ({
      teamId: membership.team.id,
      name: membership.team.name,
      role: membership.role as WorkspaceUsage["role"],
    })),
  ];

  const results = await Promise.all(
    targets.map(async (target): Promise<WorkspaceUsage | null> => {
      try {
        const response = await fetchBackend("/tasks/billing/summary", {
          userId,
          teamId: target.teamId,
          cache: "no-store",
        });
        if (!response.ok) return null;
        return { ...target, summary: (await response.json()) as BillingSummary };
      } catch {
        return null;
      }
    }),
  );

  return NextResponse.json(
    { activeTeamId, workspaces: results.filter((item): item is WorkspaceUsage => item !== null) },
    { headers: { "Cache-Control": "no-store" } },
  );
}
