import { POST } from "./route";
import { auth } from "@/lib/auth";
import prisma from "@/lib/prisma";
import { getStripeClient } from "@/lib/stripe";

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

vi.mock("@/lib/prisma", () => ({
  default: {
    user: {
      findUnique: vi.fn(),
      update: vi.fn(),
    },
  },
}));

vi.mock("@/lib/stripe", () => ({
  getStripeClient: vi.fn(),
}));

describe("/api/billing/checkout", () => {
  const env = process.env;

  beforeEach(() => {
    vi.resetAllMocks();
    process.env = {
      ...env,
      STRIPE_PRO_PRICE_ID: "price_pro",
      STRIPE_SCALE_PRICE_ID: "price_scale",
      NEXT_PUBLIC_APP_URL: "http://localhost:3107",
    };
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: "user-1", email: "user@example.com", name: "User" },
    } as never);
    vi.mocked(prisma.user.findUnique).mockResolvedValue({
      stripe_customer_id: "cus_123",
      subscription_provider: null,
      subscription_status: "inactive",
    } as never);
  });

  afterAll(() => {
    process.env = env;
  });

  it("creates checkout for the selected Scale plan", async () => {
    const stripe = {
      checkout: {
        sessions: {
          create: vi.fn().mockResolvedValue({ url: "https://checkout.example/scale" }),
        },
      },
    };
    vi.mocked(getStripeClient).mockReturnValue(stripe as never);

    const response = await POST(
      new Request("http://localhost/api/billing/checkout", {
        method: "POST",
        body: JSON.stringify({ plan: "scale" }),
      }),
    );

    expect(response.status).toBe(200);
    expect(stripe.checkout.sessions.create).toHaveBeenCalledWith(
      expect.objectContaining({
        line_items: [{ price: "price_scale", quantity: 1 }],
        metadata: { userId: "user-1", plan: "scale" },
      }),
    );
    await expect(response.json()).resolves.toEqual({ url: "https://checkout.example/scale" });
  });

  it("rejects an unknown billing plan", async () => {
    const response = await POST(
      new Request("http://localhost/api/billing/checkout", {
        method: "POST",
        body: JSON.stringify({ plan: "enterprise" }),
      }),
    );

    expect(response.status).toBe(400);
  });

  it("blocks Stripe checkout for active App Store subscriptions", async () => {
    vi.mocked(prisma.user.findUnique).mockResolvedValue({
      stripe_customer_id: null,
      subscription_provider: "apple",
      subscription_status: "active",
    } as never);

    const response = await POST(
      new Request("http://localhost/api/billing/checkout", {
        method: "POST",
        body: JSON.stringify({ plan: "pro" }),
      }),
    );

    expect(response.status).toBe(409);
    await expect(response.json()).resolves.toEqual({
      error: "Your subscription is managed through the App Store",
    });
    expect(getStripeClient).not.toHaveBeenCalled();
  });

  it("returns a configuration error when the selected plan has no Stripe price", async () => {
    process.env.STRIPE_SCALE_PRICE_ID = "";

    const response = await POST(
      new Request("http://localhost/api/billing/checkout", {
        method: "POST",
        body: JSON.stringify({ plan: "scale" }),
      }),
    );

    expect(response.status).toBe(500);
  });

  describe("for an active Stripe subscriber", () => {
    beforeEach(() => {
      vi.mocked(prisma.user.findUnique).mockResolvedValue({
        stripe_customer_id: "cus_123",
        stripe_subscription_id: "sub_123",
        subscription_provider: "stripe",
        subscription_status: "active",
      } as never);
    });

    function stripeWithSubscription(priceId: string, portalCreate = vi.fn().mockResolvedValue({ url: "https://portal.example/upgrade" })) {
      return {
        checkout: { sessions: { create: vi.fn() } },
        subscriptions: {
          retrieve: vi.fn().mockResolvedValue({ items: { data: [{ id: "si_1", price: { id: priceId } }] } }),
        },
        billingPortal: { sessions: { create: portalCreate } },
      };
    }

    it("confirms the plan change in the billing portal instead of opening a second checkout", async () => {
      const stripe = stripeWithSubscription("price_pro");
      vi.mocked(getStripeClient).mockReturnValue(stripe as never);

      const response = await POST(
        new Request("http://localhost/api/billing/checkout", {
          method: "POST",
          body: JSON.stringify({ plan: "scale" }),
        }),
      );

      expect(response.status).toBe(200);
      await expect(response.json()).resolves.toEqual({ url: "https://portal.example/upgrade" });
      expect(stripe.checkout.sessions.create).not.toHaveBeenCalled();
      expect(stripe.billingPortal.sessions.create).toHaveBeenCalledWith(
        expect.objectContaining({
          customer: "cus_123",
          flow_data: expect.objectContaining({
            type: "subscription_update_confirm",
            subscription_update_confirm: {
              subscription: "sub_123",
              items: [{ id: "si_1", price: "price_scale", quantity: 1 }],
            },
          }),
        }),
      );
    });

    it("rejects switching to the plan they already have", async () => {
      const stripe = stripeWithSubscription("price_scale");
      vi.mocked(getStripeClient).mockReturnValue(stripe as never);

      const response = await POST(
        new Request("http://localhost/api/billing/checkout", {
          method: "POST",
          body: JSON.stringify({ plan: "scale" }),
        }),
      );

      expect(response.status).toBe(409);
      expect(stripe.billingPortal.sessions.create).not.toHaveBeenCalled();
    });

    it("falls back to the plain portal when plan switching is not enabled", async () => {
      const portalCreate = vi
        .fn()
        .mockRejectedValueOnce(new Error("subscription_update is disabled"))
        .mockResolvedValueOnce({ url: "https://portal.example/home" });
      const stripe = stripeWithSubscription("price_pro", portalCreate);
      vi.mocked(getStripeClient).mockReturnValue(stripe as never);
      vi.spyOn(console, "error").mockImplementation(() => {});

      const response = await POST(
        new Request("http://localhost/api/billing/checkout", {
          method: "POST",
          body: JSON.stringify({ plan: "scale" }),
        }),
      );

      expect(response.status).toBe(200);
      await expect(response.json()).resolves.toEqual({ url: "https://portal.example/home" });
    });
  });
});
