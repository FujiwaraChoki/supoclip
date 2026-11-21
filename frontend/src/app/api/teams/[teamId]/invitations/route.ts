import { NextResponse } from "next/server";
import { getSiteUrl } from "@/lib/site";
import { fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";
import { createTeamInvitation, TeamError } from "@/server/teams";

export async function POST(
  request: Request,
  context: { params: Promise<{ teamId: string }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { teamId } = await context.params;
  let body: { email?: unknown; role?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  try {
    const { invitation, team, rawToken } = await createTeamInvitation({
      teamId,
      inviterUserId: session.user.id,
      rawEmail: body?.email,
      role: body?.role ?? "member",
    });

    const inviteUrl = `${getSiteUrl()}/invite/${encodeURIComponent(rawToken)}`;
    let emailSent = false;
    try {
      const emailResp = await fetchBackend("/teams/invite-email", {
        method: "POST",
        userId: session.user.id,
        teamId,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          team_id: team.id,
          recipient_email: invitation.email,
          team_name: team.name,
          role: invitation.role,
          invite_url: inviteUrl,
        }),
        cache: "no-store",
      });
      if (emailResp.ok) {
        const data = (await emailResp.json().catch(() => ({}))) as { status?: string };
        emailSent = data.status === "ok";
      }
    } catch (emailError) {
      console.warn("Team invitation email delivery failed (invite still created)", emailError);
    }

    return NextResponse.json(
      {
        invitation: {
          id: invitation.id,
          email: invitation.email,
          role: invitation.role,
          expiresAt: invitation.expires_at.toISOString(),
          createdAt: invitation.created_at.toISOString(),
        },
        inviteUrl,
        emailSent,
      },
      { status: 201 },
    );
  } catch (error) {
    if (error instanceof TeamError) {
      return NextResponse.json(
        { error: error.message, code: error.code, ...(error.details ?? {}) },
        { status: error.status },
      );
    }
    console.error("Failed to create team invitation", error);
    return NextResponse.json(
      { error: "Failed to create invitation" },
      { status: 500 },
    );
  }
}
