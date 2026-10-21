"use client";

import type { BillingInterval, BillingPlanId } from "@/lib/billing-plans";
import { clearCreatorCode, getStoredCreatorCode } from "@/lib/creator-code";
import { track } from "@/lib/datafast";

async function requestCheckout(plan: BillingPlanId, interval: BillingInterval, code: string | null) {
  const response = await fetch("/api/billing/checkout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(code ? { plan, interval, code } : { plan, interval }),
  });
  const data: { url?: string; error?: string; code?: string } = await response.json().catch(() => ({}));
  return { response, data };
}

/**
 * Sends the user to Stripe for `plan`, billed every `interval`. New subscribers get Checkout; existing
 * subscribers get the portal's plan-change confirmation (see the checkout route).
 * A remembered creator code is applied; if it stopped being valid it is dropped
 * and checkout continues at the regular price.
 */
export async function startUpgrade(
  plan: BillingPlanId,
  source: string,
  interval: BillingInterval = "month",
): Promise<void> {
  const code = getStoredCreatorCode();
  let { response, data } = await requestCheckout(plan, interval, code);
  // Must match INVALID_CREATOR_CODE in server/affiliates.ts (server-only module).
  if (code && data.code === "invalid_creator_code") {
    clearCreatorCode();
    ({ response, data } = await requestCheckout(plan, interval, null));
  }
  if (!response.ok || !data.url) {
    throw new Error(data.error || "We couldn't open checkout. Please try again.");
  }
  track("upgrade_started", { plan, interval, source, ...(code ? { creator_code: code } : {}) });
  window.location.href = data.url;
}
