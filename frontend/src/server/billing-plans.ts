import type { BillingInterval, BillingPlanId } from "@/lib/billing-plans";

export type ServerBillingPlan = {
  id: BillingPlanId;
  priceIds: Record<BillingInterval, string | null>;
  /** Earlier prices that existing subscribers still pay; they map to the same plan. */
  legacyPriceIds: string[];
};

function priceIdList(value: string | undefined): string[] {
  return (value || "").split(",").map((id) => id.trim()).filter(Boolean);
}

export function getServerBillingPlans(): ServerBillingPlan[] {
  return [
    {
      id: "pro",
      priceIds: {
        month: process.env.STRIPE_PRO_PRICE_ID || process.env.STRIPE_PRICE_ID || null,
        year: process.env.STRIPE_PRO_ANNUAL_PRICE_ID || null,
      },
      legacyPriceIds: priceIdList(process.env.STRIPE_PRO_LEGACY_PRICE_IDS),
    },
    {
      id: "scale",
      priceIds: {
        month: process.env.STRIPE_SCALE_PRICE_ID || null,
        year: process.env.STRIPE_SCALE_ANNUAL_PRICE_ID || null,
      },
      legacyPriceIds: priceIdList(process.env.STRIPE_SCALE_LEGACY_PRICE_IDS),
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
    (candidate) =>
      candidate.priceIds.month === priceId ||
      candidate.priceIds.year === priceId ||
      candidate.legacyPriceIds.includes(priceId)
  );
  return plan?.id || null;
}

/** Whether a price is the pre-2026-10 Pro price, whose subscribers keep 1080p. */
export function isGrandfatheredProPrice(priceId: string | null | undefined): boolean {
  if (!priceId) return false;
  return getServerBillingPlan("pro")?.legacyPriceIds.includes(priceId) ?? false;
}

export function hasAnyConfiguredStripePrice(): boolean {
  return getServerBillingPlans().some((plan) => Boolean(plan.priceIds.month || plan.priceIds.year));
}
