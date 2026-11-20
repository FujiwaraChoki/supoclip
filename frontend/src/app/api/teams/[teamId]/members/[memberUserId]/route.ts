import { NextResponse } from "next/server";
import { getServerSession } from "@/server/session";
import {
  removeTeamMember,
  TeamError,
  updateTeamMemberRole,
} from "@/server/teams";
import { WORKSPACE_COOKIE } from "@/server/workspace";

export async function PATCH(
  request: Request,
  context: { params: Promise<{ teamId: string; memberUserId: string }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { teamId, memberUserId } = await context.params;
  let body: { role?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  try {
    const updated = await updateTeamMemberRole({
      teamId,
      actorUserId: session.user.id,
      targetUserId: memberUserId,
      newRole: body?.role,
    });
    return NextResponse.json({
      member: {
        teamId: updated.team_id,
        userId: updated.user_id,
        role: updated.role,
      },
    });
  } catch (error) {
    if (error instanceof TeamError) {
      return NextResponse.json(
        { error: error.message, code: error.code },
        { status: error.status },
      );
    }
    console.error("Failed to update member role", error);
    return NextResponse.json({ error: "Failed to update role" }, { status: 500 });
  }
}

export async function DELETE(
  _request: Request,
  context: { params: Promise<{ teamId: string; memberUserId: string }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { teamId, memberUserId } = await context.params;
  try {
    await removeTeamMember({
      teamId,
      actorUserId: session.user.id,
      targetUserId: memberUserId,
    });
    const response = NextResponse.json({ removed: true });
    // If the user left the team themselves, clear the workspace cookie.
    if (memberUserId === session.user.id) {
      response.cookies.set(WORKSPACE_COOKIE, "", {
        httpOnly: true,
        sameSite: "lax",
        secure: process.env.NODE_ENV === "production",
        path: "/",
        maxAge: 0,
      });
    }
    return response;
  } catch (error) {
    if (error instanceof TeamError) {
      return NextResponse.json(
        { error: error.message, code: error.code },
        { status: error.status },
      );
    }
    console.error("Failed to remove member", error);
    return NextResponse.json({ error: "Failed to remove member" }, { status: 500 });
  }
}
