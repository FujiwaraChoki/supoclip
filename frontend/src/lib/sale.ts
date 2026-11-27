/**
 * A time-boxed sale on the Pro and Creator plans. Checkout applies the coupon
 * automatically while it runs; the landing page shows the reduced prices.
 *
 * The coupon has the same id in every Stripe account checkout can use:
 * `percent_off` = `percentOff`, `duration: "repeating"`, `duration_in_months` = 3.
 * On a monthly plan that is the first three months; on a yearly plan, the first year
 * (only the first invoice falls inside the three months).
 */
export type Sale = {
  id: string;
  couponId: string;
  percentOff: number;
  /** Inclusive start and exclusive end, UTC. */
  startsAt: Date;
  endsAt: Date;
  /** The sale's 2:1 loop for the landing page (its copy is in the video). */
  media?: { poster: string; webm: string; mp4: string };
};

const SALE_MEDIA_BASE = "https://pub-314b53c7628942c2901e78d15a8ce0b7.r2.dev";

export const BLACK_FRIDAY_2026: Sale = {
  id: "black-friday-2026",
  couponId: "blackfriday-2026",
  percentOff: 40,
  startsAt: new Date("2026-11-27T00:00:00Z"),
  // Sunday 6 December 2026, end of day Pacific time.
  endsAt: new Date("2026-12-07T08:00:00Z"),
  media: {
    poster: `${SALE_MEDIA_BASE}/black-friday-2026/black-friday-studio.webp`,
    webm: `${SALE_MEDIA_BASE}/black-friday-2026/black-friday-studio.webm`,
    mp4: `${SALE_MEDIA_BASE}/black-friday-2026/black-friday-studio.mp4`,
  },
};

export const SALES: readonly Sale[] = [BLACK_FRIDAY_2026];

/** Months a monthly subscriber pays the sale price. */
export const SALE_MONTHLY_MONTHS = 3;

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

