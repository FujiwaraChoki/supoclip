import type { BillingPlanId } from "@/lib/billing-plans";

export type ServerBillingPlan = {
  id: BillingPlanId;
  priceId: string | null;
};

export function getServerBillingPlans(): ServerBillingPlan[] {
  return [
    {
      id: "pro",
      priceId: process.env.STRIPE_PRO_PRICE_ID || process.env.STRIPE_PRICE_ID || null,
    },
    {
      id: "scale",
      priceId: process.env.STRIPE_SCALE_PRICE_ID || null,
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

  const plan = getServerBillingPlans().find((candidate) => candidate.priceId === priceId);
  return plan?.id || null;
}

export function hasAnyConfiguredStripePrice(): boolean {
  return getServerBillingPlans().some((plan) => Boolean(plan.priceId));
}
