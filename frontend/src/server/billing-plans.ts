import type { BillingInterval, BillingPlanId } from "@/lib/billing-plans";
import { getTeamBillingConfig } from "@/lib/billing-plans";
import {
  checkoutStripeAccountId,
  stripeAccountRole,
  stripeAccountsEnabled,
  stripePriceEnvPrefix,
} from "@/lib/stripe-accounts";
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
  /** Earlier prices existing teams still pay; they keep the Team plan and their seats. */
  legacyBase: string[];
  legacySeat: string[];
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

export function getTeamStripePrices(prefix: string = stripePriceEnvPrefix()): TeamStripePrices {
  return {
    base: {
      month: process.env[`${prefix}TEAM_PRICE_ID`] || null,
      year: process.env[`${prefix}TEAM_ANNUAL_PRICE_ID`] || null,
    },
    seat: {
      month: process.env[`${prefix}TEAM_SEAT_PRICE_ID`] || null,
      year: process.env[`${prefix}TEAM_SEAT_ANNUAL_PRICE_ID`] || null,
    },
    legacyBase: priceIdList(process.env[`${prefix}TEAM_LEGACY_PRICE_IDS`]),
    legacySeat: priceIdList(process.env[`${prefix}TEAM_SEAT_LEGACY_PRICE_IDS`]),
  };
}

/**
 * Whether new team workspaces can be created here. Self-hosted installs have no
 * billing, so always. A hosted install needs the Team base price: without it a
 * team could be created and filled with invitations but never subscribed, so
 * the feature stays closed until its prices are configured.
 */
export function teamPlanAvailable(monetizationEnabled: boolean): boolean {
  if (!monetizationEnabled) return true;
  const { base } = getTeamStripePrices(checkoutPriceEnvPrefix());
  return Boolean(base.month || base.year);
}

/**
 * The price variables of the Stripe account a new checkout goes to. Unlike
 * `stripePriceEnvPrefix()`, this needs no request account scope: it answers
 * "could a team subscribe here?" from routes that never talk to Stripe. With
 * two accounts configured and checkout on the destination, those prices are
 * the `STRIPE_DESTINATION_*` ones.
 */
function checkoutPriceEnvPrefix(): string {
  if (!stripeAccountsEnabled()) return "STRIPE_";
  try {
    return stripeAccountRole(checkoutStripeAccountId()) === "destination"
      ? "STRIPE_DESTINATION_"
      : "STRIPE_";
  } catch {
    // Accounts are switched on but not configured: checkout would fail too.
    return "STRIPE_UNCONFIGURED_";
  }
}

export function isTeamBaseStripePrice(priceId: string | null | undefined): boolean {
  if (!priceId) return false;
  const prices = getTeamStripePrices();
  return (
    prices.base.month === priceId ||
    prices.base.year === priceId ||
    prices.legacyBase.includes(priceId)
  );
}

export function isTeamSeatStripePrice(priceId: string | null | undefined): boolean {
  if (!priceId) return false;
  const prices = getTeamStripePrices();
  return (
    prices.seat.month === priceId ||
    prices.seat.year === priceId ||
    prices.legacySeat.includes(priceId)
  );
}

/**
 * A subscription pays for the Team plan only when it carries a Team base
 * price. Anything else on a team's Stripe customer (a price swapped in the
 * billing portal or the dashboard) does not grant the pooled allowance.
 */
export function hasTeamBasePrice(subscription: Pick<Stripe.Subscription, "items">): boolean {
  return (subscription.items?.data ?? []).some((item) => isTeamBaseStripePrice(item.price?.id));
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
