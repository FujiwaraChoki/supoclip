import { getPlanIdForStripePrice } from "@/server/billing-plans";
import { getPrismaClient } from "@/server/prisma";
import Stripe from "stripe";

const PAID_SUBSCRIPTION_STATUSES = new Set(["active", "trialing"]);

function toDate(unixSeconds: number | null | undefined): Date | null {
  if (!unixSeconds) {
    return null;
  }
  return new Date(unixSeconds * 1000);
}

export function getPaidSubscriptionPlan(subscription: Stripe.Subscription): "pro" | "scale" | null {
  if (!PAID_SUBSCRIPTION_STATUSES.has(subscription.status)) {
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
    },
  });
}
