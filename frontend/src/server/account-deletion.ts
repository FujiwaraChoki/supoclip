import { APIError } from "better-auth/api";
import { monetizationEnabled } from "@/lib/monetization";
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
 * Tasks, generated clips, sessions, accounts and API keys cascade from `users`.
 */
export async function prepareAccountDeletion(userId: string): Promise<void> {
  const prisma = getPrismaClient();

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
