/**
 * Stripe subscription statuses that must not start a second Checkout subscription.
 * `incomplete_expired`, `canceled`, and `paused` are intentionally absent: those
 * customers are free to subscribe again.
 */
export const CHECKOUT_BLOCKING_STATUSES = [
  "past_due",
  "unpaid",
  "incomplete",
  "active",
  "trialing",
] as const;

export type CheckoutBlockingStatus = (typeof CHECKOUT_BLOCKING_STATUSES)[number];

/** Renewal failed, retries are exhausted, or the first invoice was never paid. */
export const PAYMENT_ACTION_STATUSES = ["past_due", "unpaid", "incomplete"] as const;

export const EXISTING_SUBSCRIPTION_NEEDS_PAYMENT = "existing_subscription_needs_payment";

export const EXISTING_SUBSCRIPTION_NEEDS_PAYMENT_MESSAGE =
  "Your last payment failed. Update your payment method to keep your plan.";

export function subscriptionNeedsPayment(status: string | null | undefined): boolean {
  return status === "past_due" || status === "unpaid" || status === "incomplete";
}

/**
 * A Stripe renewal that failed is `past_due` while Smart Retries run. The
 * customer still has the plan, so access stays until retries end (`unpaid`
 * or `canceled`). App Store `past_due` is different: RevenueCat only sets it
 * after the grace period, and that does not keep access.
 */
export function hasPaidAccess(
  plan: string | null | undefined,
  status: string | null | undefined,
  provider?: string | null,
): boolean {
  if (plan !== "pro" && plan !== "scale") return false;
  if (status === "active" || status === "trialing") return true;
  return status === "past_due" && provider === "stripe";
}

/** Stripe payment needs the customer portal, not a new subscription checkout. */
export function stripePaymentNeedsUpdate(
  billing:
    | { subscription_status?: string | null; subscription_provider?: string | null }
    | null
    | undefined,
): boolean {
  if (!billing || !subscriptionNeedsPayment(billing.subscription_status)) return false;
  if (billing.subscription_provider === "apple" || billing.subscription_provider === "affiliate") {
    return false;
  }
  return billing.subscription_provider === "stripe" || billing.subscription_provider == null;
}
