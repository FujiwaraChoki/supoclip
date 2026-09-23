import { POST } from "./route";
import { auth } from "@/lib/auth";
import { getPrismaClient } from "@/server/prisma";
import { getServerStripeClient } from "@/server/stripe";
import { upsertSubscriptionState } from "@/server/stripe-subscription-sync";

vi.mock("next/headers", () => ({
  headers: vi.fn().mockResolvedValue(new Headers()),
}));

vi.mock("@/lib/monetization", () => ({
  monetizationEnabled: true,
}));

vi.mock("@/lib/auth", () => ({
  auth: {
    api: {
      getSession: vi.fn(),
    },
  },
}));

vi.mock("@/server/prisma", () => ({
  getPrismaClient: vi.fn(),
}));

vi.mock("@/server/stripe", () => ({
  getServerStripeClient: vi.fn(),
}));

vi.mock("@/server/stripe-subscription-sync", () => ({
  upsertSubscriptionState: vi.fn(),
}));

function mockUser(user: Record<string, unknown> | null) {
  vi.mocked(getPrismaClient).mockReturnValue({
    user: { findUnique: vi.fn().mockResolvedValue(user) },
  } as never);
}

describe("/api/billing/resume", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: "user-1", email: "user@example.com" },
    } as never);
  });

  it("clears cancel_at_period_end and syncs the subscription", async () => {
    mockUser({ stripe_subscription_id: "sub_123", subscription_provider: "stripe" });
    const resumed = { id: "sub_123", status: "active", cancel_at_period_end: false, cancel_at: null };
    const update = vi.fn().mockResolvedValue(resumed);
    vi.mocked(getServerStripeClient).mockReturnValue({
      subscriptions: {
        retrieve: vi.fn().mockResolvedValue({
          id: "sub_123",
          status: "active",
          cancel_at_period_end: true,
          cancel_at: 1792540866,
        }),
        update,
      },
    } as never);

    const response = await POST();

    expect(response.status).toBe(200);
    expect(update).toHaveBeenCalledWith("sub_123", { cancel_at_period_end: false });
    expect(upsertSubscriptionState).toHaveBeenCalledWith(resumed);
  });

  it("unsets an explicit cancel_at date", async () => {
    mockUser({ stripe_subscription_id: "sub_123", subscription_provider: "stripe" });
    const update = vi.fn().mockResolvedValue({ id: "sub_123", status: "active" });
    vi.mocked(getServerStripeClient).mockReturnValue({
      subscriptions: {
        retrieve: vi.fn().mockResolvedValue({
          id: "sub_123",
          status: "active",
          cancel_at_period_end: false,
          cancel_at: 1792540866,
        }),
        update,
      },
    } as never);

    const response = await POST();

    expect(response.status).toBe(200);
    expect(update).toHaveBeenCalledWith("sub_123", { cancel_at: "" });
  });

  it("refuses subscriptions that have already ended", async () => {
    mockUser({ stripe_subscription_id: "sub_123", subscription_provider: "stripe" });
    const update = vi.fn();
    vi.mocked(getServerStripeClient).mockReturnValue({
      subscriptions: {
        retrieve: vi.fn().mockResolvedValue({ id: "sub_123", status: "canceled" }),
        update,
      },
    } as never);

    const response = await POST();

    expect(response.status).toBe(409);
    expect(update).not.toHaveBeenCalled();
  });

  it("rejects users without a Stripe subscription", async () => {
    mockUser({ stripe_subscription_id: null, subscription_provider: "apple" });

    const response = await POST();

    expect(response.status).toBe(400);
    expect(getServerStripeClient).not.toHaveBeenCalled();
  });
});
