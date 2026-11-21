import { stripeAccountsEnabled } from "@/lib/stripe-accounts";
import { APIError } from "better-auth/api";
import { monetizationEnabled } from "@/lib/monetization";
import { deactivatePromotionCode } from "@/server/affiliates";
import { fetchBackend } from "@/server/backend-api";
import { getPrismaClient } from "@/server/prisma";

// Stripe keeps billing (and retrying failed charges) in these states.
const BILLABLE_STRIPE_STATUSES = new Set(["active", "trialing", "past_due", "unpaid"]);

export const ACTIVE_STRIPE_SUBSCRIPTION_MESSAGE =
  "You have an active subscription. Cancel it from Manage Billing before deleting your account.";

/**
 * Runs before Better Auth deletes a user (`user.deleteUser.beforeDelete`).
 *
 * Throws when the user still has a Stripe subscription that would keep
 * charging them. Otherwise removes the user's video sources: `sources` has no
 * user column, so the `users -> tasks` cascade would leave them orphaned.
 * Tasks, generated clips, sessions, accounts, API keys and social accounts
 * cascade from `users`; feedback submissions are kept, anonymized. Work done
 * inside someone else's team stays with that team.
 */
export async function prepareAccountDeletion(userId: string): Promise<void> {
  const prisma = getPrismaClient();

  if (stripeAccountsEnabled()) {
    const migration = await prisma.stripeAccountMigration.findUnique({ where: { user_id: userId } });
    if (migration && !["destination_active", "cancelled", "rollback_verified"].includes(migration.stage)) throw new APIError("BAD_REQUEST", { message: "Cancel the billing transfer from Manage Billing before deleting your account." });
  }

  if (monetizationEnabled) {
    const user = await prisma.user.findUnique({
      where: { id: userId },
      select: {
        subscription_provider: true,
        subscription_status: true,
        stripe_subscription_id: true,
        subscription_cancel_at: true,
      },
    });

    // App Store subscriptions can't be cancelled by us; the dialog tells those
    // users to cancel in the App Store instead.
    if (
      user?.stripe_subscription_id &&
      user.subscription_provider !== "apple" &&
      BILLABLE_STRIPE_STATUSES.has(user.subscription_status) &&
      !user.subscription_cancel_at
    ) {
      throw new APIError("BAD_REQUEST", { message: ACTIVE_STRIPE_SUBSCRIPTION_MESSAGE });
    }
  }

  // Prevent deleting an account that still owns a team with an active subscription or other members.
  const ownedTeams = await prisma.team.findMany({
    where: { owner_id: userId },
    select: {
      id: true,
      name: true,
      stripe_subscription_id: true,
      subscription_status: true,
      subscription_cancel_at: true,
      _count: { select: { members: true } },
    },
  });
  for (const team of ownedTeams) {
    if (
      monetizationEnabled &&
      team.stripe_subscription_id &&
      BILLABLE_STRIPE_STATUSES.has(team.subscription_status) &&
      !team.subscription_cancel_at
    ) {
      throw new APIError("BAD_REQUEST", {
        message: `Team "${team.name}" has an active subscription. Cancel it or transfer team ownership before deleting your account.`,
      });
    }
    if (team._count.members > 1) {
      throw new APIError("BAD_REQUEST", {
        message: `You own team "${team.name}" which still has other members. Transfer ownership or remove them before deleting your account.`,
      });
    }
  }

  // What this person made in a team they don't own belongs to that team. Hand
  // it to the team's owner, or it would cascade away with the user and take
  // the team's clips and scheduled posts with it. Usage records keep the id.
  await prisma.$executeRaw`
    UPDATE tasks SET user_id = teams.owner_id FROM teams
    WHERE tasks.team_id = teams.id AND tasks.user_id = ${userId} AND teams.owner_id <> ${userId}`;
  await prisma.$executeRaw`
    UPDATE social_posts SET user_id = teams.owner_id FROM teams
    WHERE social_posts.team_id = teams.id AND social_posts.user_id = ${userId} AND teams.owner_id <> ${userId}`;

  // The affiliates row cascades away with the user; free its code in Stripe so it can be reused.
  const affiliate = await prisma.affiliate.findUnique({
    where: { user_id: userId },
    select: { stripe_promotion_code_id: true },
  });
  if (affiliate?.stripe_promotion_code_id) {
    await deactivatePromotionCode(affiliate.stripe_promotion_code_id);
  }

  // Social account rows cascade away with the user; revoke their grants at the
  // platforms first, while the backend can still decrypt the tokens. The
  // personal workspace is named explicitly: the active workspace may be a team,
  // whose connections must survive this person leaving. Teams this person owns
  // alone go with the account, so theirs are revoked too.
  await revokeSocialConnections(userId, null);
  for (const team of ownedTeams) {
    await revokeSocialConnections(userId, team.id);
  }

  // PostgreSQL atomically anonymizes feedback snapshots when the user is deleted.

  // Only sources no other user's task points at.
  await prisma.source.deleteMany({
    where: {
      tasks: {
        some: { user_id: userId },
        none: { user_id: { not: userId } },
      },
    },
  });
}

async function revokeSocialConnections(userId: string, teamId: string | null): Promise<void> {
  try {
    const response = await fetchBackend("/social/connections", { method: "DELETE", userId, teamId });
    if (!response.ok) {
      console.error(`Failed to revoke social connections (HTTP ${response.status})`);
    }
  } catch (error) {
    console.error("Failed to revoke social connections", error);
  }
}
