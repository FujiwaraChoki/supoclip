import { NextResponse } from "next/server";
import { monetizationEnabled } from "@/lib/monetization";
import { BillingAccountHoldError } from "@/lib/stripe-accounts";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";
import { updateTeamSeats, withTeamBillingAccount } from "@/server/stripe-team-billing";
import { canManageBilling, TeamError } from "@/server/teams";

export async function POST(
  request: Request,
  context: { params: Promise<{ teamId: string }> },
) {
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
      { error: "Only the team owner can manage team seats" },
      { status: 403 },
    );
  }

  let body: { seats?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const seats = Number(body?.seats);
  try {
    const result = await withTeamBillingAccount(teamId, () =>
      updateTeamSeats({
        teamId,
        newSeats: seats,
        monetizationEnabled,
      }),
    );
    return NextResponse.json(result);
  } catch (error) {
    if (error instanceof TeamError) {
      return NextResponse.json(
        { error: error.message, code: error.code, ...(error.details ?? {}) },
        { status: error.status },
      );
    }
    if (error instanceof BillingAccountHoldError) {
      return NextResponse.json(
        { error: error.message, code: "billing_transfer_pending" },
        { status: 409 },
      );
    }
    console.error("Failed to update team seats", error);
    return NextResponse.json(
      { error: "Failed to update team seats" },
      { status: 500 },
    );
  }
}
