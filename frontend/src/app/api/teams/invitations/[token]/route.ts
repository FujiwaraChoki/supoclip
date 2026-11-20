import { NextResponse } from "next/server";
import { getServerSession } from "@/server/session";
import { lookupTeamInvitation, normalizeEmail } from "@/server/teams";

export async function GET(
  _request: Request,
  context: { params: Promise<{ token: string }> },
) {
  const { token } = await context.params;
  const invitation = await lookupTeamInvitation(token);
  if (!invitation) {
    return NextResponse.json({ error: "Invitation not found" }, { status: 404 });
  }

  const session = await getServerSession().catch(() => null);
  const signedInEmail = session?.user?.email ?? null;
  const emailMatches = Boolean(
    signedInEmail && normalizeEmail(signedInEmail) === normalizeEmail(invitation.email),
  );

  return NextResponse.json(
    {
      invitation,
      signedIn: Boolean(session?.user?.id),
      signedInEmail,
      emailMatches,
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
