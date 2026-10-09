"use client";

import type { BillingPlanId } from "@/lib/billing-plans";
import { clearCreatorCode, getStoredCreatorCode } from "@/lib/creator-code";
import { track } from "@/lib/datafast";

async function requestCheckout(plan: BillingPlanId, code: string | null) {
  const response = await fetch("/api/billing/checkout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(code ? { plan, code } : { plan }),
  });
  const data: { url?: string; error?: string } = await response.json().catch(() => ({}));
  return { response, data };
}

/**
 * Sends the user to Stripe for `plan`. New subscribers get Checkout; existing
 * subscribers get the portal's plan-change confirmation (see the checkout route).
 * A remembered creator code is applied; if it stopped being valid it is dropped
 * and checkout continues at the regular price.
 */
export async function startUpgrade(plan: BillingPlanId, source: string): Promise<void> {
  const code = getStoredCreatorCode();
  let { response, data } = await requestCheckout(plan, code);
  if (code && response.status === 400) {
    clearCreatorCode();
    ({ response, data } = await requestCheckout(plan, null));
  }
  if (!response.ok || !data.url) {
    throw new Error(data.error || "We couldn't open checkout. Please try again.");
  }
  track("upgrade_started", { plan, source, ...(code ? { creator_code: code } : {}) });
  window.location.href = data.url;
}
