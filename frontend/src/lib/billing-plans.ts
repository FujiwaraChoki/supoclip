export type BillingPlanId = "pro" | "scale";

export type PublicBillingPlan = {
  id: BillingPlanId;
  name: string;
  priceMonthly: string;
  generationLimit: number;
  description: string;
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

  return [
    {
      id: "pro",
      name: "Pro",
      priceMonthly: proPriceMonthly,
      generationLimit: proLimit,
      description: "For creators who clip consistently each month.",
      cta: "Upgrade to Pro",
      highlighted: true,
    },
    {
      id: "scale",
      name: "Scale",
      priceMonthly: scalePriceMonthly,
      generationLimit: scaleLimit,
      description: "For teams and high-volume publishing workflows.",
      cta: "Upgrade to Scale",
      highlighted: false,
    },
  ];
}
