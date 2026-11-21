import { NextResponse } from "next/server";
import { monetizationEnabled } from "@/lib/monetization";
import { BillingAccountHoldError } from "@/lib/stripe-accounts";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";
import {
  createTeamBillingPortalSession,
  withTeamBillingAccount,
} from "@/server/stripe-team-billing";
import { canManageBilling, TeamError } from "@/server/teams";

export async function POST(
  _request: Request,
  context: { params: Promise<{ teamId: string }> },
) {
  if (!monetizationEnabled) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { teamId } = await context.params;
  const membership = await getPrismaClient().teamMember.findUnique({
    where: { team_id_user_id: { team_id: teamId, user_id: session.user.id } },
  });
  if (!membership || !canManageBilling(membership.role)) {
    return NextResponse.json(
      { error: "Only the team owner can manage team billing" },
      { status: 403 },
    );
  }

  try {
    const result = await withTeamBillingAccount(teamId, () =>
      createTeamBillingPortalSession({ teamId }),
    );
    return NextResponse.json(result);
  } catch (error) {
    if (error instanceof TeamError) {
      return NextResponse.json(
        { error: error.message, code: error.code },
        { status: error.status },
      );
    }
    if (error instanceof BillingAccountHoldError) {
      return NextResponse.json(
        { error: error.message, code: "billing_transfer_pending" },
        { status: 409 },
      );
    }
    console.error("Failed to create team billing portal session", error);
    return NextResponse.json(
      { error: "Unable to open billing portal" },
      { status: 500 },
    );
  }
}
