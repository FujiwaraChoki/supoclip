import { NextResponse } from "next/server";
import { monetizationEnabled } from "@/lib/monetization";
import { teamPlanAvailable } from "@/server/billing-plans";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";
import { createTeamForUser, TeamError } from "@/server/teams";
import {
  getActiveTeamIdFromCookies,
  getVerifiedActiveTeamId,
  WORKSPACE_COOKIE,
} from "@/server/workspace";

const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365;

export async function GET() {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const prisma = getPrismaClient();
  const now = new Date();
  const [memberships, activeTeamId] = await Promise.all([
    prisma.teamMember.findMany({
      where: { user_id: session.user.id },
      include: {
        team: {
          include: {
            _count: { select: { members: true } },
            invitations: {
              where: {
                accepted_at: null,
                revoked_at: null,
                expires_at: { gt: now },
              },
              select: { id: true },
            },
          },
        },
      },
      orderBy: { created_at: "asc" },
    }),
    getVerifiedActiveTeamId(session.user.id),
  ]);

  const teams = memberships.map((membership) => ({
    id: membership.team.id,
    name: membership.team.name,
    slug: membership.team.slug,
    ownerId: membership.team.owner_id,
    role: membership.role,
    plan: membership.team.plan,
    subscriptionStatus: membership.team.subscription_status,
    seatLimit: membership.team.seat_limit,
    memberCount: membership.team._count.members,
    pendingInviteCount: membership.team.invitations.length,
    occupiedSeats:
      membership.team._count.members + membership.team.invitations.length,
  }));

  const response = NextResponse.json(
    { activeTeamId, teams, available: teamPlanAvailable(monetizationEnabled) },
    { headers: { "Cache-Control": "no-store" } },
  );
  // A cookie left behind by a removed membership, a deleted team or another
  // account on this browser is dropped, so it is not carried for a year.
  if (!activeTeamId && (await getActiveTeamIdFromCookies())) {
    response.cookies.set(WORKSPACE_COOKIE, "", {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: 0,
    });
  }
  return response;
}

export async function POST(request: Request) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let body: { name?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  if (!teamPlanAvailable(monetizationEnabled)) {
    return NextResponse.json(
      { error: "Team workspaces aren't available yet.", code: "teams_unavailable" },
      { status: 503 },
    );
  }

  try {
    const team = await createTeamForUser(session.user.id, body?.name);
    const response = NextResponse.json(
      {
        team: {
          id: team.id,
          name: team.name,
          slug: team.slug,
          ownerId: team.owner_id,
          role: "owner",
          plan: team.plan,
          subscriptionStatus: team.subscription_status,
          seatLimit: team.seat_limit,
          memberCount: 1,
          pendingInviteCount: 0,
          occupiedSeats: 1,
        },
      },
      { status: 201 },
    );
    response.cookies.set(WORKSPACE_COOKIE, team.id, {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: COOKIE_MAX_AGE_SECONDS,
    });
    return response;
  } catch (error) {
    if (error instanceof TeamError) {
      return NextResponse.json(
        { error: error.message, code: error.code, ...(error.details ?? {}) },
        { status: error.status },
      );
    }
    console.error("Failed to create team", error);
    return NextResponse.json({ error: "Failed to create team" }, { status: 500 });
  }
}
