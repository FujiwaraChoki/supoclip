export type BillingPlanId = "pro" | "scale";
export type AnyPaidBillingPlanId = BillingPlanId | "team";
export type BillingInterval = "month" | "year";

export const MAX_TEAM_SEATS = 100;

export type PublicBillingPlan = {
  id: BillingPlanId;
  name: string;
  priceMonthly: string;
  /** Price of a year, billed up front. */
  priceAnnual: string;
  /** Minutes of source video allowed each month. */
  minuteLimit: number;
  /** Output height label, e.g. "1080p". */
  resolution: string;
  scheduling: boolean;
  youtubeMaxMinutes: number;
  description: string;
  highlights: string[];
  cta: string;
  highlighted: boolean;
};

export type TeamBillingConfig = {
  id: "team";
  name: string;
  priceMonthly: string;
  priceAnnual: string;
  seatPriceMonthly: string;
  seatPriceAnnual: string;
  includedSeats: number;
  baseMinuteLimit: number;
  extraSeatMinuteLimit: number;
  youtubeMaxMinutes: number;
  aiEditLimit: number;
  resolution: string;
  scheduling: boolean;
};

export type TeamPricingBreakdown = {
  seats: number;
  includedSeats: number;
  extraSeats: number;
  interval: BillingInterval;
  basePrice: number;
  seatPrice: number;
  extraSeatsSubtotal: number;
  totalPrice: number;
  monthlyEquivalent: number;
  annualFullPrice: number;
  monthsFree: number;
  totalMinutes: number;
  totalHours: number;
  aiEditLimit: number;
};

export const PAID_PLAN_IDS: BillingPlanId[] = ["pro", "scale"];

export function billingPlanMessageKey(
  plan: string | null | undefined,
): "free" | "pro" | "scale" | "team" | "selfHost" {
  if (plan === "pro" || plan === "scale" || plan === "team") return plan;
  if (plan === "self_host") return "selfHost";
  return "free";
}

export function formatBillingPlanName(plan: string | null | undefined): string {
  if (plan === "pro") {
    return "Pro";
  }
  if (plan === "scale") {
    return "Creator";
  }
  if (plan === "team") {
    return "Team";
  }
  if (plan === "self_host") {
    return "Self-Hosted";
  }
  return "Free";
}

export function isPaidBillingPlan(
  plan: string | null | undefined,
): plan is AnyPaidBillingPlanId {
  return plan === "pro" || plan === "scale" || plan === "team";
}

export type SocialPublishingStage = "off" | "soon" | "live";

/**
 * How plan pages present scheduled posting (a Creator/Team feature):
 * hidden ("off"), marked "Coming soon" (default, while platform reviews are
 * pending), or listed as a live feature ("live"). Set NEXT_PUBLIC_SOCIAL_PUBLISHING.
 */
export function socialPublishingStage(): SocialPublishingStage {
  const value = process.env.NEXT_PUBLIC_SOCIAL_PUBLISHING?.trim().toLowerCase();
  if (value === "off" || value === "0") return "off";
  if (value === "live" || value === "1") return "live";
  return "soon";
}

export function isBillingInterval(value: unknown): value is BillingInterval {
  return value === "month" || value === "year";
}

function parsePositiveInt(raw: string | undefined, fallback: number): number {
  const parsed = parseInt(raw || "", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function getTeamBillingConfig(): TeamBillingConfig {
  return {
    id: "team",
    name: "Team",
    priceMonthly: process.env.NEXT_PUBLIC_TEAM_PRICE_MONTHLY || "69",
    priceAnnual: process.env.NEXT_PUBLIC_TEAM_PRICE_ANNUAL || "690",
    seatPriceMonthly: process.env.NEXT_PUBLIC_TEAM_SEAT_PRICE_MONTHLY || "15",
    seatPriceAnnual: process.env.NEXT_PUBLIC_TEAM_SEAT_PRICE_ANNUAL || "150",
    includedSeats: parsePositiveInt(process.env.NEXT_PUBLIC_TEAM_INCLUDED_SEATS, 3),
    baseMinuteLimit: parsePositiveInt(process.env.NEXT_PUBLIC_TEAM_PLAN_MINUTE_LIMIT, 3600),
    extraSeatMinuteLimit: parsePositiveInt(
      process.env.NEXT_PUBLIC_TEAM_EXTRA_SEAT_MINUTE_LIMIT,
      900,
    ),
    youtubeMaxMinutes: parsePositiveInt(process.env.NEXT_PUBLIC_TEAM_YOUTUBE_MAX_MINUTES, 180),
    aiEditLimit: parsePositiveInt(process.env.NEXT_PUBLIC_TEAM_AI_EDIT_LIMIT, 300),
    resolution: "1080p",
    scheduling: true,
  };
}

export function formatCurrencyAmount(amount: number): string {
  return Number.isInteger(amount) ? String(amount) : amount.toFixed(2);
}

export function calculateTeamPricing(
  requestedSeats: number,
  interval: BillingInterval = "month",
  config: TeamBillingConfig = getTeamBillingConfig(),
): TeamPricingBreakdown {
  const clampedSeats = Math.min(
    MAX_TEAM_SEATS,
    Math.max(config.includedSeats, Math.floor(Number.isFinite(requestedSeats) ? requestedSeats : config.includedSeats)),
  );
  const extraSeats = Math.max(0, clampedSeats - config.includedSeats);
  const monthlyBase = Number(config.priceMonthly) || 69;
  const annualBase = Number(config.priceAnnual) || 690;
  const monthlySeat = Number(config.seatPriceMonthly) || 15;
  const annualSeat = Number(config.seatPriceAnnual) || 150;

  const basePrice = interval === "year" ? annualBase : monthlyBase;
  const seatPrice = interval === "year" ? annualSeat : monthlySeat;
  const extraSeatsSubtotal = extraSeats * seatPrice;
  const totalPrice = basePrice + extraSeatsSubtotal;

  const monthlyTotal = monthlyBase + extraSeats * monthlySeat;
  const annualFullPrice = monthlyTotal * 12;
  const annualTotal = annualBase + extraSeats * annualSeat;
  const monthsFree = monthlyTotal > 0 ? Math.round(12 - annualTotal / monthlyTotal) : 0;
  const monthlyEquivalent = interval === "year" ? totalPrice / 12 : totalPrice;
  const totalMinutes = config.baseMinuteLimit + extraSeats * config.extraSeatMinuteLimit;

  return {
    seats: clampedSeats,
    includedSeats: config.includedSeats,
    extraSeats,
    interval,
    basePrice,
    seatPrice,
    extraSeatsSubtotal,
    totalPrice,
    monthlyEquivalent,
    annualFullPrice,
    monthsFree,
    totalMinutes,
    totalHours: Math.round((totalMinutes / 60) * 10) / 10,
    aiEditLimit: config.aiEditLimit,
  };
}

export function getPublicBillingPlans(): PublicBillingPlan[] {
  const proPriceMonthly = process.env.NEXT_PUBLIC_PRO_PRICE_MONTHLY || "9.99";
  const proPriceAnnual = process.env.NEXT_PUBLIC_PRO_PRICE_ANNUAL || "99.90";
  const scalePriceMonthly = process.env.NEXT_PUBLIC_SCALE_PRICE_MONTHLY || "24.99";
  const scalePriceAnnual = process.env.NEXT_PUBLIC_SCALE_PRICE_ANNUAL || "249.90";
  // Mirrors the backend's PRO_/SCALE_PLAN_MINUTE_LIMIT defaults.
  const proMinutes = parseInt(process.env.NEXT_PUBLIC_PRO_PLAN_MINUTE_LIMIT || "600", 10);
  const scaleMinutes = parseInt(process.env.NEXT_PUBLIC_SCALE_PLAN_MINUTE_LIMIT || "1500", 10);
  // Per-video length cap, any source. Mirrors the backend's PRO_/SCALE_YOUTUBE_MAX_VIDEO_DURATION defaults.
  const proYoutubeMinutes = parseInt(process.env.NEXT_PUBLIC_PRO_YOUTUBE_MAX_MINUTES || "120", 10);
  const scaleYoutubeMinutes = parseInt(process.env.NEXT_PUBLIC_SCALE_YOUTUBE_MAX_MINUTES || "180", 10);

  return [
    {
      id: "pro",
      name: "Pro",
      priceMonthly: proPriceMonthly,
      priceAnnual: proPriceAnnual,
      minuteLimit: proMinutes,
      resolution: "720p",
      scheduling: false,
      youtubeMaxMinutes: proYoutubeMinutes,
      description: "For creators who clip consistently each month.",
      highlights: [
        `${formatMinutes(proMinutes)} of video every month`,
        "720p clips",
        `Videos up to ${formatMinutes(proYoutubeMinutes)} each`,
        "Custom fonts, B-roll and caption templates",
      ],
      cta: "Upgrade to Pro",
      highlighted: true,
    },
    {
      id: "scale",
      name: "Creator",
      priceMonthly: scalePriceMonthly,
      priceAnnual: scalePriceAnnual,
      minuteLimit: scaleMinutes,
      resolution: "1080p",
      scheduling: true,
      youtubeMaxMinutes: scaleYoutubeMinutes,
      description: "For creators publishing every day.",
      highlights: [
        `${formatMinutes(scaleMinutes)} of video every month`,
        "Full HD 1080p clips",
        `Videos up to ${formatMinutes(scaleYoutubeMinutes)} each`,
        "Priority processing",
      ],
      cta: "Upgrade to Creator",
      highlighted: false,
    },
  ];
}

/** The plan's price for one billing interval, as shown next to "/mo" or "/yr". */
export function planPrice(plan: PublicBillingPlan, interval: BillingInterval): string {
  return interval === "year" ? plan.priceAnnual : plan.priceMonthly;
}

/** What twelve months would cost at the monthly price, e.g. "119.88"; shown struck through next to the annual price. */
export function annualFullPrice(plan: PublicBillingPlan): string {
  const full = Number(plan.priceMonthly) * 12;
  return Number.isInteger(full) ? String(full) : full.toFixed(2);
}

/** What an annual plan works out to per month, e.g. "8.33". */
export function annualMonthlyEquivalent(plan: PublicBillingPlan): string {
  const monthly = Number(plan.priceAnnual) / 12;
  return Number.isInteger(monthly) ? String(monthly) : monthly.toFixed(2);
}

/** How many months of the monthly price an annual plan saves, e.g. 2. */
export function annualMonthsFree(plan: PublicBillingPlan): number {
  const monthly = Number(plan.priceMonthly);
  if (!monthly) return 0;
  return Math.round(12 - Number(plan.priceAnnual) / monthly);
}

export function formatMinutes(minutes: number): string {
  if (minutes >= 60 && minutes % 60 === 0) {
    const hours = minutes / 60;
    return `${hours} ${hours === 1 ? "hour" : "hours"}`;
  }
  return `${minutes} minutes`;
}

/** The plan one step above `plan`, or null when there is nothing to upgrade to. */
export function getNextBillingPlan(plan: string | null | undefined): PublicBillingPlan | null {
  const plans = getPublicBillingPlans();
  if (plan === "scale" || plan === "team" || plan === "self_host") return null;
  if (plan === "pro") return plans.find((candidate) => candidate.id === "scale") ?? null;
  return plans.find((candidate) => candidate.id === "pro") ?? null;
}
