import {
  CHECKOUT_BLOCKING_STATUSES,
  hasPaidAccess,
  stripePaymentNeedsUpdate,
  subscriptionNeedsPayment,
} from "./subscription-status";

describe("subscription status", () => {
  it("blocks a second checkout for open Stripe subscriptions only", () => {
    expect(CHECKOUT_BLOCKING_STATUSES).toEqual(["past_due", "unpaid", "incomplete", "active", "trialing"]);
    for (const status of ["canceled", "incomplete_expired", "paused", "inactive"]) {
      expect(subscriptionNeedsPayment(status)).toBe(false);
    }
  });

  it("treats past_due, unpaid, and incomplete as a payment that still needs collecting", () => {
    expect(subscriptionNeedsPayment("past_due")).toBe(true);
    expect(subscriptionNeedsPayment("unpaid")).toBe(true);
    expect(subscriptionNeedsPayment("incomplete")).toBe(true);
    expect(subscriptionNeedsPayment("active")).toBe(false);
  });

  it("keeps Stripe access during the retry window and drops it once retries stop", () => {
    expect(hasPaidAccess("pro", "past_due", "stripe")).toBe(true);
    expect(hasPaidAccess("scale", "active", "stripe")).toBe(true);
    expect(hasPaidAccess("pro", "unpaid", "stripe")).toBe(false);
    expect(hasPaidAccess("pro", "incomplete", "stripe")).toBe(false);
    expect(hasPaidAccess("free", "past_due", "stripe")).toBe(false);
  });

  it("does not keep access for an App Store billing issue after the grace period", () => {
    expect(hasPaidAccess("pro", "past_due", "apple")).toBe(false);
    expect(hasPaidAccess("pro", "past_due", null)).toBe(false);
  });

  it("asks Stripe subscribers with a failed payment to update the card instead of checking out", () => {
    expect(stripePaymentNeedsUpdate({ subscription_status: "past_due", subscription_provider: "stripe" })).toBe(true);
    expect(stripePaymentNeedsUpdate({ subscription_status: "unpaid", subscription_provider: "stripe" })).toBe(true);
    expect(stripePaymentNeedsUpdate({ subscription_status: "incomplete", subscription_provider: null })).toBe(true);
    expect(stripePaymentNeedsUpdate({ subscription_status: "active", subscription_provider: "stripe" })).toBe(false);
    expect(stripePaymentNeedsUpdate({ subscription_status: "past_due", subscription_provider: "apple" })).toBe(false);
    expect(stripePaymentNeedsUpdate({ subscription_status: "past_due", subscription_provider: "affiliate" })).toBe(false);
  });
});
