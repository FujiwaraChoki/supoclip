import { beforeEach, describe, expect, it, vi } from "vitest";
import { POST } from "./route";
import { auth } from "@/lib/auth";
import { billingAccountRoute } from "@/server/stripe-account-routing";
import { getPrismaClient } from "@/server/prisma";
import { getServerStripeClient } from "@/server/stripe";
import { syncSubscriptionUpdate } from "@/server/stripe-subscription-sync";

vi.mock("next/headers", () => ({ headers: vi.fn(async () => new Headers()) }));
vi.mock("@/lib/monetization", () => ({ monetizationEnabled: true }));
vi.mock("@/lib/stripe-accounts", () => ({ stripeAccountsEnabled: () => true }));
vi.mock("@/lib/auth", () => ({ auth: { api: { getSession: vi.fn() } } }));
vi.mock("@/server/stripe-account-routing", () => ({
  billingAccountRoute: vi.fn(),
}));
vi.mock("@/server/prisma", () => ({ getPrismaClient: vi.fn() }));
vi.mock("@/server/stripe", () => ({ getServerStripeClient: vi.fn() }));
vi.mock("@/server/stripe-subscription-sync", () => ({
  syncSubscriptionUpdate: vi.fn(),
}));

describe("authenticated cancellation during transfer", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: "user_test" },
    } as never);
  });
  it("rejects unauthenticated requests before routing or Stripe", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    expect((await POST()).status).toBe(401);
    expect(billingAccountRoute).not.toHaveBeenCalled();
  });
  it("does not repeat legacy cancellation after the migration saga handled it", async () => {
    vi.mocked(billingAccountRoute).mockResolvedValue(
      Response.json({ ok: true }),
    );
    expect((await POST()).status).toBe(200);
    expect(billingAccountRoute).toHaveBeenCalledWith(
      "user_test",
      "cancel",
      expect.any(Function),
    );
    expect(getServerStripeClient).not.toHaveBeenCalled();
  });
  it("a settled owner cancels its scoped subscription and syncs the result", async () => {
    vi.mocked(billingAccountRoute).mockImplementation(
      async (_id, _action, callback) => callback(),
    );
    vi.mocked(getPrismaClient).mockReturnValue({
      user: {
        findUniqueOrThrow: vi.fn(async () => ({
          stripe_subscription_id: "sub_owned",
        })),
      },
    } as never);
    const result = {
      id: "sub_owned",
      status: "active",
      cancel_at_period_end: true,
    };
    const update = vi.fn(async () => result);
    vi.mocked(getServerStripeClient).mockReturnValue({
      subscriptions: {
        retrieve: vi.fn(async () => ({ id: "sub_owned", status: "active" })),
        update,
      },
    } as never);
    expect((await POST()).status).toBe(200);
    expect(update).toHaveBeenCalledWith("sub_owned", {
      cancel_at_period_end: true,
    });
    expect(syncSubscriptionUpdate).toHaveBeenCalledWith(result);
  });
});
