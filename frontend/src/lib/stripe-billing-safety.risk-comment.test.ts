import { describe, expect, it } from "vitest";
import type Stripe from "stripe";
import { billingRiskCancellationComment } from "./stripe-billing-safety";

const event = (type: string) => ({ id: "evt_1", type }) as Stripe.Event;

describe("billingRiskCancellationComment", () => {
  it("labels a full refund as a refund, not as risk", () => {
    expect(billingRiskCancellationComment(event("charge.refunded"))).toBe(
      "refunded:evt_1",
    );
  });
  it("labels a dispute", () => {
    expect(
      billingRiskCancellationComment(event("charge.dispute.created")),
    ).toBe("dispute:evt_1");
  });
  it("labels a fraud warning", () => {
    expect(
      billingRiskCancellationComment(
        event("radar.early_fraud_warning.created"),
      ),
    ).toBe("fraud_warning:evt_1");
  });
});
