import { NextResponse } from "next/server";
import { getServerSession } from "@/server/session";
import {
  removeTeamMember,
  TeamError,
  updateTeamMemberLimits,
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
  let body: {
    role?: unknown;
    monthlyMinuteLimit?: unknown;
    monthlyAiEditLimit?: unknown;
  };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const hasRole = body?.role !== undefined;
  const hasLimits =
    body?.monthlyMinuteLimit !== undefined || body?.monthlyAiEditLimit !== undefined;
  if (!hasRole && !hasLimits) {
    return NextResponse.json(
      { error: "Provide role or member limits to update" },
      { status: 400 },
    );
  }

  try {
    let updated = null;
    if (hasRole) {
      updated = await updateTeamMemberRole({
        teamId,
        actorUserId: session.user.id,
        targetUserId: memberUserId,
        newRole: body.role,
      });
    }
    if (hasLimits) {
      updated = await updateTeamMemberLimits({
        teamId,
        actorUserId: session.user.id,
        targetUserId: memberUserId,
        monthlyMinuteLimit: body.monthlyMinuteLimit,
        monthlyAiEditLimit: body.monthlyAiEditLimit,
      });
    }
    return NextResponse.json({
      member: {
        teamId: updated!.team_id,
        userId: updated!.user_id,
        role: updated!.role,
        monthlyMinuteLimit: updated!.monthly_minute_limit ?? null,
        monthlyAiEditLimit: updated!.monthly_ai_edit_limit ?? null,
      },
    });
  } catch (error) {
    if (error instanceof TeamError) {
      return NextResponse.json(
        { error: error.message, code: error.code },
        { status: error.status },
      );
    }
    console.error("Failed to update member", error);
    return NextResponse.json({ error: "Failed to update member" }, { status: 500 });
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
