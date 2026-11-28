import type { BillingInterval } from "@/lib/billing-plans";

/**
 * A time-boxed sale on the Pro and Creator plans. Checkout applies the coupon
 * automatically while it runs, on the billing intervals the sale covers; the landing
 * page shows the reduced prices.
 *
 * The coupon has the same id in every Stripe account checkout can use:
 * `percent_off` = `percentOff`, `duration: "repeating"`, `duration_in_months` = 3, so on a
 * yearly plan it covers the first year (only the first invoice falls inside the three
 * months). Monthly and yearly prices share a Stripe product, so the coupon itself can't be
 * limited to yearly: `intervals` is what keeps monthly checkouts at full price.
 */
export type SaleMedia = { poster: string; webm: string; mp4: string };

export type Sale = {
  id: string;
  couponId: string;
  percentOff: number;
  /** Billing intervals the sale applies to. */
  intervals: readonly BillingInterval[];
  /** Inclusive start and exclusive end, UTC. */
  startsAt: Date;
  endsAt: Date;
  /** The sale's 2:1 loop for the band under the hero (its copy is in the video). */
  media?: SaleMedia;
  /** The same scene without copy, square, for the corner note. */
  scene?: SaleMedia;
};

const SALE_MEDIA_BASE = "https://pub-314b53c7628942c2901e78d15a8ce0b7.r2.dev";

export const BLACK_FRIDAY_2026: Sale = {
  id: "black-friday-2026",
  couponId: "blackfriday-2026",
  percentOff: 40,
  intervals: ["year"],
  startsAt: new Date("2026-11-27T00:00:00Z"),
  // Sunday 6 December 2026, end of day Pacific time.
  endsAt: new Date("2026-12-07T08:00:00Z"),
  media: {
    poster: `${SALE_MEDIA_BASE}/black-friday-2026/black-friday-studio-v2.webp`,
    webm: `${SALE_MEDIA_BASE}/black-friday-2026/black-friday-studio-v2.webm`,
    mp4: `${SALE_MEDIA_BASE}/black-friday-2026/black-friday-studio-v2.mp4`,
  },
  scene: {
    poster: `${SALE_MEDIA_BASE}/black-friday-2026/black-friday-scene.webp`,
    webm: `${SALE_MEDIA_BASE}/black-friday-2026/black-friday-scene.webm`,
    mp4: `${SALE_MEDIA_BASE}/black-friday-2026/black-friday-scene.mp4`,
  },
};

export const SALES: readonly Sale[] = [BLACK_FRIDAY_2026];

export function saleApplies(sale: Sale, interval: BillingInterval): boolean {
  return sale.intervals.includes(interval);
}

export function activeSale(now: Date = new Date()): Sale | null {
  const time = now.getTime();
  return SALES.find((sale) => time >= sale.startsAt.getTime() && time < sale.endsAt.getTime()) ?? null;
}

/**
 * "9.99" at 40% off -> "5.99". Rounded to the cent the way Stripe rounds a percent coupon;
 * a whole price that stays whole keeps its format ("10" -> "6").
 */
export function salePrice(price: string, sale: Sale): string {
  const cents = Math.round(Number(price) * 100);
  const discounted = cents - Math.round((cents * sale.percentOff) / 100);
  if (!price.includes(".") && discounted % 100 === 0) return String(discounted / 100);
  return (discounted / 100).toFixed(2);
}

