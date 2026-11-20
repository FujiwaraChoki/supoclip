import { NextResponse } from "next/server";
import { isBillingInterval, type BillingInterval } from "@/lib/billing-plans";
import { monetizationEnabled } from "@/lib/monetization";
import { BillingAccountHoldError } from "@/lib/stripe-accounts";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";
import {
  createTeamCheckoutSession,
  withTeamBillingAccount,
} from "@/server/stripe-team-billing";
import { canManageBilling, TeamError } from "@/server/teams";

export async function POST(
  request: Request,
  context: { params: Promise<{ teamId: string }> },
) {
  if (!monetizationEnabled) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const session = await getServerSession();
  if (!session?.user?.id || !session.user.email) {
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

  let interval: BillingInterval = "month";
  let seats: number | undefined;
  try {
    const body = await request.json();
    if (body?.interval !== undefined) {
      if (!isBillingInterval(body.interval)) {
        return NextResponse.json({ error: "Unknown billing interval" }, { status: 400 });
      }
      interval = body.interval;
    }
    if (body?.seats !== undefined) {
      const parsed = Number(body.seats);
      if (!Number.isInteger(parsed) || parsed < 1) {
        return NextResponse.json({ error: "Invalid seat count" }, { status: 400 });
      }
      seats = parsed;
    }
  } catch {
    // Allow empty body with defaults.
  }

  try {
    const result = await withTeamBillingAccount(teamId, () =>
      createTeamCheckoutSession({
        teamId,
        ownerEmail: session.user.email,
        ownerName: session.user.name,
        actorUserId: session.user.id,
        interval,
        requestedSeats: seats,
      }),
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
    console.error("Failed to create team checkout session", error);
    return NextResponse.json(
      { error: "Unable to create checkout session" },
      { status: 500 },
    );
  }
}
