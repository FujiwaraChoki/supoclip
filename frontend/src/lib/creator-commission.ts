/**
 * Creator program commission terms. The website copy, the creator dashboard and the
 * ledger (src/server/affiliate-commissions.ts) all read these, so changing a number
 * here changes what creators are promised and what they are paid.
 */

/** Share of what a referred customer pays, after discounts and refunds and without tax. */
export const COMMISSION_PERCENT = 30;
export const COMMISSION_RATE_BPS = COMMISSION_PERCENT * 100;
/** Commission is earned on invoices paid within this many months of the customer's first payment. */
export const COMMISSION_MONTHS = 12;
/** Days a commission waits before it can be paid out, so refunds and chargebacks come off first. */
export const COMMISSION_HOLD_DAYS = 30;
/**
 * Commission is kept and paid in this currency (the US platform account's balance). A charge
 * that settled in another currency is counted in the invoice's own amount when the invoice is
 * in this currency, which is the case for every plan price.
 */
export const PAYOUT_CURRENCY = "usd";
/** Smallest payout, in minor units of the payout currency. Smaller balances roll over. */
export const MINIMUM_PAYOUT_MINOR = 2500;

/**
 * Countries a creator can be paid in: Stripe Connect cross-border payouts reach the US,
 * the UK, Canada, Switzerland and the EEA from a platform in any of those regions.
 */
export const PAYOUT_COUNTRIES = [
  "US", "GB", "CA", "CH",
  "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IS",
  "IT", "LV", "LI", "LT", "LU", "MT", "NL", "NO", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
] as const;
export type PayoutCountry = (typeof PAYOUT_COUNTRIES)[number];

export function isPayoutCountry(value: unknown): value is PayoutCountry {
  return typeof value === "string" && (PAYOUT_COUNTRIES as readonly string[]).includes(value);
}

/**
 * The part of a settled charge that commission is paid on.
 *
 * `settledAmount` is what reached our Stripe balance for the charge (its balance
 * transaction, before Stripe fees, in the settlement currency). The invoice's tax is
 * removed in proportion, so the base is the price the customer paid after discounts.
 */
export function commissionBase(settledAmount: number, invoicePaid: number, invoiceTax: number): number {
  if (settledAmount <= 0 || invoicePaid <= 0) return 0;
  const taxShare = Math.min(Math.max(invoiceTax, 0), invoicePaid) / invoicePaid;
  return Math.round(settledAmount * (1 - taxShare));
}

export function commissionAmount(base: number, rateBps = COMMISSION_RATE_BPS): number {
  return Math.round((base * rateBps) / 10_000);
}

/** Whether an invoice paid at `paidAt` is still inside the window that started at `firstPaidAt`. */
export function withinCommissionWindow(firstPaidAt: Date, paidAt: Date): boolean {
  const end = new Date(firstPaidAt);
  end.setUTCMonth(end.getUTCMonth() + COMMISSION_MONTHS);
  return paidAt.getTime() < end.getTime();
}

export function formatMinor(amount: number, currency: string, locale?: string): string {
  const code = currency.toUpperCase();
  const digits = new Intl.NumberFormat("en", { style: "currency", currency: code }).resolvedOptions()
    .maximumFractionDigits ?? 2;
  return new Intl.NumberFormat(locale, { style: "currency", currency: code }).format(amount / 10 ** digits);
}

/** The audience's creator-code discount (Stripe coupon `creator-20-3mo`). */
export const CREATOR_DISCOUNT_PERCENT = 20;
export const CREATOR_DISCOUNT_MONTHS = 3;

/** What one monthly subscriber earns a creator in their first year, in whole currency units. */
export function firstYearCommission(priceMonthly: number): number {
  const discounted = priceMonthly * (1 - CREATOR_DISCOUNT_PERCENT / 100);
  const paidMonths = Math.min(CREATOR_DISCOUNT_MONTHS, COMMISSION_MONTHS);
  const total = (discounted * paidMonths + priceMonthly * (COMMISSION_MONTHS - paidMonths)) * (COMMISSION_PERCENT / 100);
  return Math.round(total);
}
