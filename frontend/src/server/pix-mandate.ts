import type Stripe from "stripe";
import type { BillingInterval } from "@/lib/billing-plans";

/**
 * Pix (Brazil) only appears on a subscription Checkout when the session carries Pix Automático mandate
 * options: the most a renewal may charge and how often. Without them Stripe falls back to a R$400 monthly
 * mandate, which yearly prices exceed. The cap is the price's BRL amount with headroom, so tax or a later
 * price change doesn't make renewals fail. Stripe rejects `amount_type` in subscription mode, and the
 * options are ignored when the customer pays in another currency.
 */
const MANDATE_HEADROOM = 2;
const CACHE_TTL_MS = 10 * 60 * 1000;

type PixMandateOptions = { amount: number; payment_schedule: "monthly" | "yearly" };

const brlAmounts = new Map<string, { amount: number | null; fetchedAt: number }>();

async function brlUnitAmount(stripe: Stripe, priceId: string): Promise<number | null> {
  const cached = brlAmounts.get(priceId);
  if (cached && Date.now() - cached.fetchedAt < CACHE_TTL_MS) return cached.amount;
  const price = await stripe.prices.retrieve(priceId, { expand: ["currency_options"] });
  const amount =
    price.currency === "brl" ? price.unit_amount : (price.currency_options?.brl?.unit_amount ?? null);
  brlAmounts.set(priceId, { amount, fetchedAt: Date.now() });
  return amount;
}

/**
 * Pix options for a subscription Checkout, or nothing when the price has no BRL amount. A failed lookup
 * never blocks checkout: the session is created without Pix.
 */
export async function pixPaymentMethodOptions(
  stripe: Stripe,
  priceId: string,
  interval: BillingInterval,
): Promise<Stripe.Checkout.SessionCreateParams.PaymentMethodOptions | undefined> {
  let amount: number | null;
  try {
    amount = await brlUnitAmount(stripe, priceId);
  } catch (error) {
    console.warn("Pix mandate: could not read the BRL price", error instanceof Error ? error.message : error);
    return undefined;
  }
  if (!amount) return undefined;
  const mandate: PixMandateOptions = {
    amount: amount * MANDATE_HEADROOM,
    payment_schedule: interval === "year" ? "yearly" : "monthly",
  };
  // stripe-node 20.4 types don't list `pix.mandate_options` yet; the API accepts it.
  return { pix: { mandate_options: mandate } } as Stripe.Checkout.SessionCreateParams.PaymentMethodOptions;
}

/** Test helper. */
export function clearPixMandateCache() {
  brlAmounts.clear();
}
