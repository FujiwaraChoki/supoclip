import { NextResponse } from "next/server";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";
import { WORKSPACE_COOKIE } from "@/server/workspace";

const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365;

export async function POST(request: Request) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let body: { teamId?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const teamId =
    typeof body?.teamId === "string" && body.teamId.trim()
      ? body.teamId.trim()
      : null;

  if (teamId) {
    const membership = await getPrismaClient().teamMember.findUnique({
      where: { team_id_user_id: { team_id: teamId, user_id: session.user.id } },
      select: { team_id: true, role: true },
    });
    if (!membership) {
      return NextResponse.json(
        { error: "You are not a member of this team" },
        { status: 403 },
      );
    }
  }

  const response = NextResponse.json({ activeTeamId: teamId });
  if (teamId) {
    response.cookies.set(WORKSPACE_COOKIE, teamId, {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: COOKIE_MAX_AGE_SECONDS,
    });
  } else {
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
