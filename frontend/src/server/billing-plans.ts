import type { BillingInterval, BillingPlanId } from "@/lib/billing-plans";

export type ServerBillingPlan = {
  id: BillingPlanId;
  priceIds: Record<BillingInterval, string | null>;
};

export function getServerBillingPlans(): ServerBillingPlan[] {
  return [
    {
      id: "pro",
      priceIds: {
        month: process.env.STRIPE_PRO_PRICE_ID || process.env.STRIPE_PRICE_ID || null,
        year: process.env.STRIPE_PRO_ANNUAL_PRICE_ID || null,
      },
    },
    {
      id: "scale",
      priceIds: {
        month: process.env.STRIPE_SCALE_PRICE_ID || null,
        year: process.env.STRIPE_SCALE_ANNUAL_PRICE_ID || null,
      },
    },
  ];
}

export function getServerBillingPlan(planId: string): ServerBillingPlan | null {
  return getServerBillingPlans().find((plan) => plan.id === planId) || null;
}

export function getPlanIdForStripePrice(priceId: string | null | undefined): BillingPlanId | null {
  if (!priceId) {
    return null;
  }

  const plan = getServerBillingPlans().find(
    (candidate) => candidate.priceIds.month === priceId || candidate.priceIds.year === priceId
  );
  return plan?.id || null;
}

export function hasAnyConfiguredStripePrice(): boolean {
  return getServerBillingPlans().some((plan) => Boolean(plan.priceIds.month || plan.priceIds.year));
}
