import { getPlanIdForStripePrice, isGrandfatheredProPrice } from "@/server/billing-plans";
import { getPrismaClient } from "@/server/prisma";
import { getServerStripeClient } from "@/server/stripe";
import Stripe from "stripe";

// past_due stays entitled: Stripe is still retrying the invoice. unpaid and
// incomplete are not — retries are over, or the first invoice was never paid.
const ENTITLED_SUBSCRIPTION_STATUSES = new Set(["active", "trialing", "past_due"]);

function toDate(unixSeconds: number | null | undefined): Date | null {
  if (!unixSeconds) {
    return null;
  }
  return new Date(unixSeconds * 1000);
}

export function getPaidSubscriptionPlan(subscription: Stripe.Subscription): "pro" | "scale" | null {
  if (!ENTITLED_SUBSCRIPTION_STATUSES.has(subscription.status)) {
    return null;
  }

  for (const item of subscription.items.data) {
    const plan = getPlanIdForStripePrice(item.price?.id);
    if (plan) {
      return plan;
    }
  }

  return null;
}

function getSubscriptionPeriod(subscription: Stripe.Subscription): {
  currentPeriodStart: number | null;
  currentPeriodEnd: number | null;
} {
  const starts = subscription.items.data
    .map((item) => item.current_period_start)
    .filter((value): value is number => typeof value === "number");
  const ends = subscription.items.data
    .map((item) => item.current_period_end)
    .filter((value): value is number => typeof value === "number");

  return {
    currentPeriodStart: starts.length > 0 ? Math.min(...starts) : null,
    currentPeriodEnd: ends.length > 0 ? Math.max(...ends) : null,
  };
}

export async function upsertSubscriptionState(subscription: Stripe.Subscription) {
  const prisma = getPrismaClient();
  const customerId = typeof subscription.customer === "string" ? subscription.customer : subscription.customer.id;
  const subscriptionId = subscription.id;
  const status = subscription.status;
  const { currentPeriodStart, currentPeriodEnd } = getSubscriptionPeriod(subscription);
  // Portal cancellations set cancel_at_period_end (and usually cancel_at too).
  const cancelAt =
    subscription.cancel_at ?? (subscription.cancel_at_period_end ? currentPeriodEnd : null);

  const paidPlan = getPaidSubscriptionPlan(subscription);
  const plan = paidPlan || "free";
  // Subscribers still on the pre-2026-10 Pro price keep 1080p, even if they
  // were past due when the flag was backfilled.
  const onGrandfatheredPrice =
    paidPlan === "pro" && subscription.items.data.some((item) => isGrandfatheredProPrice(item.price?.id));

  // A non-paid Stripe state must not clobber an entitlement owned by the App
  // Store; only a paid Stripe grant may take over from an Apple subscription.
  const providerFilter = paidPlan
    ? {}
    : { OR: [{ subscription_provider: "stripe" }, { subscription_provider: null }] };

  await prisma.user.updateMany({
    where: { stripe_customer_id: customerId, ...providerFilter },
    data: {
      plan,
      subscription_status: status,
      subscription_provider: "stripe",
      stripe_subscription_id: subscriptionId,
      billing_period_start: toDate(currentPeriodStart),
      billing_period_end: toDate(currentPeriodEnd),
      trial_ends_at: toDate(subscription.trial_end),
      subscription_cancel_at: toDate(cancelAt),
      // Grandfathered 1080p ends once the subscription is cancelled; a failed
      // payment (past_due/unpaid) keeps it so recovering customers retain it.
      ...(onGrandfatheredPrice
        ? { legacy_hd: true }
        : status === "canceled" || status === "incomplete_expired"
          ? { legacy_hd: false }
          : {}),
    },
  });
}

/**
 * Rows written before past_due kept its plan still say `free`. The next
 * subscription webhook would fix them; this applies the same sync on read so
 * a customer already inside the retry window is not stuck on Free.
 * Returns true when the stored subscription was rewritten and the caller
 * should reload billing.
 */
export async function restorePastDueEntitlement(userId: string): Promise<boolean> {
  const prisma = getPrismaClient();
  const user = await prisma.user.findUnique({
    where: { id: userId },
    select: {
      plan: true,
      subscription_status: true,
      subscription_provider: true,
      stripe_subscription_id: true,
    },
  });
  if (
    !user?.stripe_subscription_id ||
    user.subscription_provider !== "stripe" ||
    user.subscription_status !== "past_due" ||
    user.plan === "pro" ||
    user.plan === "scale"
  ) {
    return false;
  }

  const subscription = await getServerStripeClient().subscriptions.retrieve(user.stripe_subscription_id);
  await upsertSubscriptionState(subscription);
  return true;
}
