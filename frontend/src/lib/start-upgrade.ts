"use client";

import type { BillingPlanId } from "@/lib/billing-plans";
import { track } from "@/lib/datafast";

/**
 * Sends the user to Stripe for `plan`. New subscribers get Checkout; existing
 * subscribers get the portal's plan-change confirmation (see the checkout route).
 */
export async function startUpgrade(plan: BillingPlanId, source: string): Promise<void> {
  const response = await fetch("/api/billing/checkout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan }),
  });
  const data: { url?: string; error?: string } = await response.json().catch(() => ({}));
  if (!response.ok || !data.url) {
    throw new Error(data.error || "We couldn't open checkout. Please try again.");
  }
  track("upgrade_started", { plan, source });
  window.location.href = data.url;
}
