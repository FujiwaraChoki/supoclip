import type Stripe from "stripe";
import type { BillingInterval } from "@/lib/billing-plans";
import { activeSale, saleApplies, type Sale } from "@/lib/sale";

const CACHE_MS = 5 * 60 * 1000;
// Keyed by the Stripe client, which is per account: the coupon must exist in the account that bills.
const cache = new WeakMap<Stripe, Map<string, { ok: boolean; at: number }>>();

/**
 * The running sale whose coupon this Stripe account can apply to this billing interval, or null.
 * A missing or expired coupon never blocks checkout: the customer pays full price
 * and the error is logged so the coupon can be created.
 */
export async function checkoutSale(
  stripe: Stripe,
  interval: BillingInterval,
  now: Date = new Date(),
): Promise<Sale | null> {
  const sale = activeSale(now);
  if (!sale || !saleApplies(sale, interval)) return null;
  let entries = cache.get(stripe);
  if (!entries) {
    entries = new Map();
    cache.set(stripe, entries);
  }
  const cached = entries.get(sale.couponId);
  if (cached && now.getTime() - cached.at < CACHE_MS) return cached.ok ? sale : null;

  let ok = false;
  try {
    const coupon = await stripe.coupons.retrieve(sale.couponId);
    ok = coupon.valid && coupon.percent_off === sale.percentOff;
    if (!ok) console.error(`Sale coupon ${sale.couponId} is not valid in this Stripe account`);
  } catch (error) {
    console.error(`Sale coupon ${sale.couponId} could not be read; checking out at full price`, error);
  }
  entries.set(sale.couponId, { ok, at: now.getTime() });
  return ok ? sale : null;
}

export function clearSaleCouponCache(stripe: Stripe): void {
  cache.delete(stripe);
}
