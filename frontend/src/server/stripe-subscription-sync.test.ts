import { getPaidSubscriptionPlan, restorePastDueEntitlement } from "./stripe-subscription-sync";
import { getPrismaClient } from "@/server/prisma";
import { getServerStripeClient } from "@/server/stripe";
import type Stripe from "stripe";

vi.mock("@/server/prisma", () => ({
  getPrismaClient: vi.fn(),
}));

vi.mock("@/server/stripe", () => ({
  getServerStripeClient: vi.fn(),
}));

function subscription(status: Stripe.Subscription.Status, priceId = "price_pro"): Stripe.Subscription {
  return {
    id: "sub_123",
    customer: "cus_123",
    status,
    items: { data: [{ price: { id: priceId } }] },
  } as Stripe.Subscription;
}

describe("getPaidSubscriptionPlan", () => {
  beforeEach(() => {
    process.env.STRIPE_PRO_PRICE_ID = "price_pro";
    process.env.STRIPE_SCALE_PRICE_ID = "price_scale";
  });

  it("keeps the plan while a renewal is past due", () => {
    expect(getPaidSubscriptionPlan(subscription("past_due", "price_scale"))).toBe("scale");
  });

  it.each(["unpaid", "incomplete", "canceled", "incomplete_expired"] as const)(
    "does not treat %s as a paid plan",
    (status) => {
      expect(getPaidSubscriptionPlan(subscription(status))).toBeNull();
    },
  );
});

describe("restorePastDueEntitlement", () => {
  const updateMany = vi.fn().mockResolvedValue({ count: 1 });

  beforeEach(() => {
    vi.resetAllMocks();
    process.env.STRIPE_PRO_PRICE_ID = "price_pro";
    updateMany.mockResolvedValue({ count: 1 });
  });

  it("rewrites a free past_due row from the live Stripe subscription", async () => {
    const retrieve = vi.fn().mockResolvedValue(subscription("past_due"));
    vi.mocked(getServerStripeClient).mockReturnValue({ subscriptions: { retrieve } } as never);
    vi.mocked(getPrismaClient).mockReturnValue({
      user: {
        findUnique: vi.fn().mockResolvedValue({
          plan: "free",
          subscription_status: "past_due",
          subscription_provider: "stripe",
          stripe_subscription_id: "sub_123",
        }),
        updateMany,
      },
    } as never);

    await expect(restorePastDueEntitlement("user-1")).resolves.toBe(true);
    expect(retrieve).toHaveBeenCalledWith("sub_123");
    expect(updateMany).toHaveBeenCalledWith(
      expect.objectContaining({
        data: expect.objectContaining({ plan: "pro", subscription_status: "past_due", subscription_provider: "stripe" }),
      }),
    );
  });

  it("leaves an unpaid subscription unpaid", async () => {
    const retrieve = vi.fn().mockResolvedValue(subscription("unpaid"));
    vi.mocked(getServerStripeClient).mockReturnValue({ subscriptions: { retrieve } } as never);
    vi.mocked(getPrismaClient).mockReturnValue({
      user: {
        findUnique: vi.fn().mockResolvedValue({
          plan: "free",
          subscription_status: "past_due",
          subscription_provider: "stripe",
          stripe_subscription_id: "sub_123",
        }),
        updateMany,
      },
    } as never);

    await expect(restorePastDueEntitlement("user-1")).resolves.toBe(true);
    expect(updateMany).toHaveBeenCalledWith(
      expect.objectContaining({
        data: expect.objectContaining({ plan: "free", subscription_status: "unpaid" }),
      }),
    );
  });

  it("does not call Stripe when the stored plan is already paid", async () => {
    vi.mocked(getPrismaClient).mockReturnValue({
      user: {
        findUnique: vi.fn().mockResolvedValue({
          plan: "pro",
          subscription_status: "past_due",
          subscription_provider: "stripe",
          stripe_subscription_id: "sub_123",
        }),
      },
    } as never);

    await expect(restorePastDueEntitlement("user-1")).resolves.toBe(false);
    expect(getServerStripeClient).not.toHaveBeenCalled();
  });
});
