import { NextResponse } from "next/server";
import { getSiteUrl } from "@/lib/site";
import { fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";
import {
  resendTeamInvitation,
  revokeTeamInvitation,
  TeamError,
} from "@/server/teams";

export async function POST(
  _request: Request,
  context: { params: Promise<{ teamId: string; invitationId: string }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { teamId, invitationId } = await context.params;
  try {
    const { invitation, team, rawToken } = await resendTeamInvitation({
      teamId,
      invitationId,
      actorUserId: session.user.id,
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
      console.warn("Team invitation resend email failed", emailError);
    }

    return NextResponse.json({
      invitation: {
        id: invitation.id,
        email: invitation.email,
        role: invitation.role,
        expiresAt: invitation.expires_at.toISOString(),
      },
      inviteUrl,
      emailSent,
    });
  } catch (error) {
    if (error instanceof TeamError) {
      return NextResponse.json(
        { error: error.message, code: error.code },
        { status: error.status },
      );
    }
    console.error("Failed to resend invitation", error);
    return NextResponse.json({ error: "Failed to resend invitation" }, { status: 500 });
  }
}

export async function DELETE(
  _request: Request,
  context: { params: Promise<{ teamId: string; invitationId: string }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { teamId, invitationId } = await context.params;
  try {
    await revokeTeamInvitation({
      teamId,
      invitationId,
      actorUserId: session.user.id,
    });
    return NextResponse.json({ revoked: true });
  } catch (error) {
    if (error instanceof TeamError) {
      return NextResponse.json(
        { error: error.message, code: error.code },
        { status: error.status },
      );
    }
    console.error("Failed to revoke invitation", error);
    return NextResponse.json({ error: "Failed to revoke invitation" }, { status: 500 });
  }
}
