import { NextResponse } from "next/server";
import { monetizationEnabled } from "@/lib/monetization";
import { fetchBackend } from "@/server/backend-api";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";
import {
  teamHasBillableStripeSubscription,
  withTeamBillingAccount,
} from "@/server/stripe-team-billing";
import {
  canManageBilling,
  canManageMembers,
  TeamError,
  transferTeamOwnership,
  validateTeamName,
} from "@/server/teams";
import { WORKSPACE_COOKIE } from "@/server/workspace";

const BILLABLE_STATUSES = new Set(["active", "trialing", "past_due", "unpaid"]);

export async function GET(
  _request: Request,
  context: { params: Promise<{ teamId: string }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { teamId } = await context.params;
  const prisma = getPrismaClient();
  const membership = await prisma.teamMember.findUnique({
    where: { team_id_user_id: { team_id: teamId, user_id: session.user.id } },
  });
  if (!membership) {
    return NextResponse.json({ error: "Not a member of this team" }, { status: 403 });
  }

  const now = new Date();
  const team = await prisma.team.findUnique({
    where: { id: teamId },
    include: {
      members: {
        include: {
          user: { select: { id: true, name: true, email: true, image: true } },
        },
        orderBy: { created_at: "asc" },
      },
      invitations: {
        where: {
          accepted_at: null,
          revoked_at: null,
          expires_at: { gt: now },
        },
        include: {
          inviter: { select: { id: true, name: true, email: true } },
        },
        orderBy: { created_at: "desc" },
      },
    },
  });

  if (!team) {
    return NextResponse.json({ error: "Team not found" }, { status: 404 });
  }

  // Pull pooled video-minute, AI-edit, and per-member usage from the backend (best effort).
  let usage: {
    billing: unknown;
    ai_edits: unknown;
    member_usage?: Record<string, { usage_minutes?: number; ai_edits_used?: number }>;
  } | null = null;
  try {
    const upstream = await fetchBackend(`/teams/${encodeURIComponent(teamId)}/usage`, {
      method: "GET",
      userId: session.user.id,
      teamId,
      cache: "no-store",
    });
    if (upstream.ok) {
      usage = (await upstream.json()) as {
        billing: unknown;
        ai_edits: unknown;
        member_usage?: Record<string, { usage_minutes?: number; ai_edits_used?: number }>;
      };
    }
  } catch (error) {
    console.warn("Could not load backend team usage summary", error);
  }

  const isManager = canManageMembers(membership.role);
  const memberUsageMap = usage?.member_usage ?? {};
  return NextResponse.json(
    {
      team: {
        id: team.id,
        name: team.name,
        slug: team.slug,
        ownerId: team.owner_id,
        plan: team.plan,
        subscriptionStatus: team.subscription_status,
        subscriptionProvider: team.subscription_provider,
        seatLimit: team.seat_limit,
        billingPeriodStart: team.billing_period_start?.toISOString() ?? null,
        billingPeriodEnd: team.billing_period_end?.toISOString() ?? null,
        cancelAt: team.subscription_cancel_at?.toISOString() ?? null,
        hasStripeCustomer: Boolean(team.stripe_customer_id),
        hasStripeSubscription: Boolean(team.stripe_subscription_id),
        createdAt: team.created_at.toISOString(),
      },
      currentUserRole: membership.role,
      permissions: {
        canManageMembers: isManager,
        canManageBilling: canManageBilling(membership.role),
        canDeleteTeam: membership.role === "owner",
      },
      seats: {
        limit: team.seat_limit,
        activeMembers: team.members.length,
        pendingInvitations: team.invitations.length,
        occupied: team.members.length + team.invitations.length,
      },
      members: team.members.map((member) => {
        const canViewUsage = isManager || member.user.id === session.user.id;
        const memberStats = canViewUsage ? memberUsageMap[member.user.id] : undefined;
        return {
          userId: member.user.id,
          name: member.user.name,
          email: member.user.email,
          image: member.user.image,
          role: member.role,
          joinedAt: member.created_at.toISOString(),
          monthlyMinuteLimit: canViewUsage ? (member.monthly_minute_limit ?? null) : null,
          monthlyAiEditLimit: canViewUsage ? (member.monthly_ai_edit_limit ?? null) : null,
          usageMinutes: canViewUsage ? (memberStats?.usage_minutes ?? 0) : null,
          usageAiEdits: canViewUsage ? (memberStats?.ai_edits_used ?? 0) : null,
        };
      }),
      invitations: isManager
        ? team.invitations.map((invite) => ({
            id: invite.id,
            email: invite.email,
            role: invite.role,
            invitedBy: invite.inviter?.name || invite.inviter?.email || null,
            expiresAt: invite.expires_at.toISOString(),
            createdAt: invite.created_at.toISOString(),
          }))
        : [],
      usage,
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}

export async function PATCH(
  request: Request,
  context: { params: Promise<{ teamId: string }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { teamId } = await context.params;
  let body: { name?: unknown; transferOwnerId?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const prisma = getPrismaClient();
  const membership = await prisma.teamMember.findUnique({
    where: { team_id_user_id: { team_id: teamId, user_id: session.user.id } },
  });
  if (!membership || !canManageMembers(membership.role)) {
    return NextResponse.json(
      { error: "Only team owners and admins can update team settings" },
      { status: 403 },
    );
  }

  try {
    if (typeof body.transferOwnerId === "string" && body.transferOwnerId.trim()) {
      await transferTeamOwnership({
        teamId,
        actorUserId: session.user.id,
        newOwnerUserId: body.transferOwnerId.trim(),
      });
    }
    if (body.name !== undefined) {
      const name = validateTeamName(body.name);
      await prisma.team.update({
        where: { id: teamId },
        data: { name },
      });
    }
    const updated = await prisma.team.findUnique({
      where: { id: teamId },
      select: { id: true, name: true, slug: true, owner_id: true },
    });
    return NextResponse.json({ team: updated });
  } catch (error) {
    if (error instanceof TeamError) {
      return NextResponse.json(
        { error: error.message, code: error.code },
        { status: error.status },
      );
    }
    console.error("Failed to update team", error);
    return NextResponse.json({ error: "Failed to update team" }, { status: 500 });
  }
}

export async function DELETE(
  _request: Request,
  context: { params: Promise<{ teamId: string }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { teamId } = await context.params;
  const prisma = getPrismaClient();
  const membership = await prisma.teamMember.findUnique({
    where: { team_id_user_id: { team_id: teamId, user_id: session.user.id } },
  });
  if (!membership || membership.role !== "owner") {
    return NextResponse.json(
      { error: "Only the team owner can delete the team" },
      { status: 403 },
    );
  }

  const team = await prisma.team.findUnique({
    where: { id: teamId },
    select: {
      id: true,
      stripe_subscription_id: true,
      subscription_status: true,
      subscription_cancel_at: true,
    },
  });
  if (!team) {
    return NextResponse.json({ error: "Team not found" }, { status: 404 });
  }

  const activeSubscription = NextResponse.json(
    {
      error:
        "Cancel the team subscription in Manage Billing before deleting this team.",
      code: "active_subscription",
    },
    { status: 409 },
  );
  if (
    monetizationEnabled &&
    team.stripe_subscription_id &&
    BILLABLE_STATUSES.has(team.subscription_status) &&
    !team.subscription_cancel_at
  ) {
    return activeSubscription;
  }
  if (monetizationEnabled) {
    // The row can be behind Stripe (a webhook still on its way). Deleting the
    // team would leave that subscription charging with nothing to cancel it from.
    try {
      const billable = await withTeamBillingAccount(teamId, () =>
        teamHasBillableStripeSubscription(teamId),
      );
      if (billable) return activeSubscription;
    } catch (error) {
      console.error("Could not check the team's Stripe subscriptions before deletion", error);
      return NextResponse.json(
        { error: "Couldn't check this team's billing. Try again in a moment." },
        { status: 503 },
      );
    }
  }

  // Revoke any team social tokens before deleting the team row.
  await fetchBackend("/social/connections", {
    method: "DELETE",
    userId: session.user.id,
    teamId,
  }).catch(() => {});

  // `sources` has no team column: remove the ones only this team's tasks use,
  // together with the team whose tasks then cascade away.
  await prisma.$transaction(async (tx) => {
    await tx.source.deleteMany({
      where: {
        tasks: {
          some: { team_id: teamId },
          none: { OR: [{ team_id: null }, { team_id: { not: teamId } }] },
        },
      },
    });
    await tx.team.delete({ where: { id: teamId } });
  });

  const response = NextResponse.json({ deleted: true });
  response.cookies.set(WORKSPACE_COOKIE, "", {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 0,
  });
  return response;
}
