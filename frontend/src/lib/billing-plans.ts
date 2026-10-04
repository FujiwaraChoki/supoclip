export type BillingPlanId = "pro" | "scale";

export type PublicBillingPlan = {
  id: BillingPlanId;
  name: string;
  priceMonthly: string;
  generationLimit: number;
  youtubeMaxMinutes: number;
  description: string;
  highlights: string[];
  cta: string;
  highlighted: boolean;
};

export const PAID_PLAN_IDS: BillingPlanId[] = ["pro", "scale"];

export function formatBillingPlanName(plan: string | null | undefined): string {
  if (plan === "pro") {
    return "Pro";
  }
  if (plan === "scale") {
    return "Scale";
  }
  if (plan === "self_host") {
    return "Self-Hosted";
  }
  return "Free";
}

export function isPaidBillingPlan(plan: string | null | undefined): plan is BillingPlanId {
  return plan === "pro" || plan === "scale";
}

export function getPublicBillingPlans(): PublicBillingPlan[] {
  const proPriceMonthly = process.env.NEXT_PUBLIC_PRO_PRICE_MONTHLY || "10";
  const scalePriceMonthly = process.env.NEXT_PUBLIC_SCALE_PRICE_MONTHLY || "50";
  const proLimit = parseInt(process.env.NEXT_PUBLIC_PRO_PLAN_TASK_LIMIT || "50", 10);
  const scaleLimit = parseInt(process.env.NEXT_PUBLIC_SCALE_PLAN_TASK_LIMIT || "300", 10);
  // Mirrors the backend's PRO_/SCALE_YOUTUBE_MAX_VIDEO_DURATION defaults.
  const proYoutubeMinutes = parseInt(process.env.NEXT_PUBLIC_PRO_YOUTUBE_MAX_MINUTES || "90", 10);
  const scaleYoutubeMinutes = parseInt(process.env.NEXT_PUBLIC_SCALE_YOUTUBE_MAX_MINUTES || "180", 10);

  return [
    {
      id: "pro",
      name: "Pro",
      priceMonthly: proPriceMonthly,
      generationLimit: proLimit,
      youtubeMaxMinutes: proYoutubeMinutes,
      description: "For creators who clip consistently each month.",
      highlights: [
        `${proLimit} videos clipped every month`,
        `YouTube videos up to ${formatMinutes(proYoutubeMinutes)}`,
        "Custom fonts, B-roll and caption templates",
      ],
      cta: "Upgrade to Pro",
      highlighted: true,
    },
    {
      id: "scale",
      name: "Scale",
      priceMonthly: scalePriceMonthly,
      generationLimit: scaleLimit,
      youtubeMaxMinutes: scaleYoutubeMinutes,
      description: "For teams and high-volume publishing workflows.",
      highlights: [
        `${scaleLimit} videos clipped every month`,
        `YouTube videos up to ${formatMinutes(scaleYoutubeMinutes)}`,
        "Priority processing",
      ],
      cta: "Upgrade to Scale",
      highlighted: false,
    },
  ];
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
  if (plan === "scale" || plan === "self_host") return null;
  if (plan === "pro") return plans.find((candidate) => candidate.id === "scale") ?? null;
  return plans.find((candidate) => candidate.id === "pro") ?? null;
}
