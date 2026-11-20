import type { BillingInterval, BillingPlanId } from "@/lib/billing-plans";
import { getTeamBillingConfig } from "@/lib/billing-plans";
import { stripePriceEnvPrefix } from "@/lib/stripe-accounts";
import type Stripe from "stripe";

export type ServerBillingPlan = {
  id: BillingPlanId;
  priceIds: Record<BillingInterval, string | null>;
  /** Earlier prices that existing subscribers still pay; they map to the same plan. */
  legacyPriceIds: string[];
};

export type TeamStripePrices = {
  base: Record<BillingInterval, string | null>;
  seat: Record<BillingInterval, string | null>;
};

function priceIdList(value: string | undefined): string[] {
  return (value || "").split(",").map((id) => id.trim()).filter(Boolean);
}

export function getServerBillingPlans(): ServerBillingPlan[] {
  const prefix = stripePriceEnvPrefix();
  return [
    {
      id: "pro",
      priceIds: {
        month: process.env[`${prefix}PRO_PRICE_ID`] || process.env[`${prefix}PRICE_ID`] || null,
        year: process.env[`${prefix}PRO_ANNUAL_PRICE_ID`] || null,
      },
      legacyPriceIds: priceIdList(process.env[`${prefix}PRO_LEGACY_PRICE_IDS`]),
    },
    {
      id: "scale",
      priceIds: {
        month: process.env[`${prefix}SCALE_PRICE_ID`] || null,
        year: process.env[`${prefix}SCALE_ANNUAL_PRICE_ID`] || null,
      },
      legacyPriceIds: priceIdList(process.env[`${prefix}SCALE_LEGACY_PRICE_IDS`]),
    },
  ];
}

export function getTeamStripePrices(): TeamStripePrices {
  const prefix = stripePriceEnvPrefix();
  return {
    base: {
      month: process.env[`${prefix}TEAM_PRICE_ID`] || null,
      year: process.env[`${prefix}TEAM_ANNUAL_PRICE_ID`] || null,
    },
    seat: {
      month: process.env[`${prefix}TEAM_SEAT_PRICE_ID`] || null,
      year: process.env[`${prefix}TEAM_SEAT_ANNUAL_PRICE_ID`] || null,
    },
  };
}

export function isTeamBaseStripePrice(priceId: string | null | undefined): boolean {
  if (!priceId) return false;
  const prices = getTeamStripePrices();
  return prices.base.month === priceId || prices.base.year === priceId;
}

export function isTeamSeatStripePrice(priceId: string | null | undefined): boolean {
  if (!priceId) return false;
  const prices = getTeamStripePrices();
  return prices.seat.month === priceId || prices.seat.year === priceId;
}

export function isTeamStripePrice(priceId: string | null | undefined): boolean {
  return isTeamBaseStripePrice(priceId) || isTeamSeatStripePrice(priceId);
}

export function getTeamIntervalForStripePrice(
  priceId: string | null | undefined,
): BillingInterval | null {
  if (!priceId) return null;
  const prices = getTeamStripePrices();
  if (prices.base.year === priceId || prices.seat.year === priceId) return "year";
  if (prices.base.month === priceId || prices.seat.month === priceId) return "month";
  return null;
}

export function getTeamSeatsFromSubscription(
  subscription: Pick<Stripe.Subscription, "items">,
  includedSeats: number = getTeamBillingConfig().includedSeats,
): number {
  let extraSeats = 0;
  for (const item of subscription.items?.data ?? []) {
    if (isTeamSeatStripePrice(item.price?.id)) {
      extraSeats += Math.max(0, Number(item.quantity ?? 0));
    }
  }
  return Math.max(includedSeats, includedSeats + extraSeats);
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
  const soloConfigured = getServerBillingPlans().some((plan) =>
    Boolean(plan.priceIds.month || plan.priceIds.year),
  );
  const team = getTeamStripePrices();
  const teamConfigured = Boolean(team.base.month || team.base.year);
  return soloConfigured || teamConfigured;
}
