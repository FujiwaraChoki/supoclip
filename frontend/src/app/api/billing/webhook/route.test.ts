import { POST } from "./route";
import { fetchBackend } from "@/server/backend-api";
import { getPrismaClient } from "@/server/prisma";
import { getServerStripeClient } from "@/server/stripe";

vi.mock("@/lib/monetization", () => ({
  monetizationEnabled: true,
}));

vi.mock("@/server/prisma", () => ({
  getPrismaClient: vi.fn(),
}));

vi.mock("@/server/stripe", () => ({
  getServerStripeClient: vi.fn(),
}));

vi.mock("@/server/backend-api", () => ({
  fetchBackend: vi.fn(),
}));

describe("/api/billing/webhook", () => {
  const env = process.env;

  beforeEach(() => {
    vi.resetAllMocks();
    process.env = {
      ...env,
      STRIPE_WEBHOOK_SECRET: "whsec_test",
      STRIPE_PRO_PRICE_ID: "price_pro",
      STRIPE_SCALE_PRICE_ID: "price_scale",
    };
  });

  afterAll(() => {
    process.env = env;
  });

  it("rejects requests without a Stripe signature", async () => {
    const response = await POST(
      new Request("http://localhost/api/billing/webhook", { method: "POST" }),
    );

    expect(response.status).toBe(400);
  });

  it("treats duplicate events as idempotent", async () => {
    const stripe = {
      webhooks: {
        constructEvent: vi.fn().mockReturnValue({
          id: "evt_1",
          type: "checkout.session.completed",
          data: { object: { mode: "payment" } },
        }),
      },
    };

    vi.mocked(getServerStripeClient).mockReturnValue(stripe as never);
    vi.mocked(getPrismaClient).mockReturnValue({
      stripeWebhookEvent: {
        create: vi.fn().mockRejectedValue({ code: "P2002" }),
      },
    } as never);

    const response = await POST(
      new Request("http://localhost/api/billing/webhook", {
        method: "POST",
        headers: { "stripe-signature": "sig" },
        body: "{}",
      }),
    );

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ ok: true });
  });

  it("notifies the backend when a subscription is deleted", async () => {
    const deleteEvent = vi.fn().mockResolvedValue({});
    const stripe = {
      webhooks: {
        constructEvent: vi.fn().mockReturnValue({
          id: "evt_2",
          type: "customer.subscription.deleted",
          data: {
            object: {
              customer: "cus_123",
            },
          },
        }),
      },
    };

    vi.mocked(getServerStripeClient).mockReturnValue(stripe as never);
    vi.mocked(getPrismaClient).mockReturnValue({
      stripeWebhookEvent: {
        create: vi.fn().mockResolvedValue({}),
        delete: deleteEvent,
      },
      user: {
        findFirst: vi.fn().mockResolvedValue({ id: "user-1" }),
        updateMany: vi.fn().mockResolvedValue({ count: 1 }),
      },
    } as never);
    vi.mocked(fetchBackend).mockResolvedValue(new Response("{}", { status: 200 }));

    const response = await POST(
      new Request("http://localhost/api/billing/webhook", {
        method: "POST",
        headers: { "stripe-signature": "sig" },
        body: "{}",
      }),
    );

    expect(fetchBackend).toHaveBeenCalled();
    expect(response.status).toBe(200);
    expect(deleteEvent).not.toHaveBeenCalled();
  });

  it("acknowledges subscription deletions even when backend email delivery fails", async () => {
    const deleteEvent = vi.fn().mockResolvedValue({});
    const stripe = {
      webhooks: {
        constructEvent: vi.fn().mockReturnValue({
          id: "evt_3",
          type: "customer.subscription.deleted",
          data: {
            object: {
              customer: "cus_123",
            },
          },
        }),
      },
    };

    vi.mocked(getServerStripeClient).mockReturnValue(stripe as never);
    vi.mocked(getPrismaClient).mockReturnValue({
      stripeWebhookEvent: {
        create: vi.fn().mockResolvedValue({}),
        delete: deleteEvent,
      },
      user: {
        findFirst: vi.fn().mockResolvedValue({ id: "user-1" }),
        updateMany: vi.fn().mockResolvedValue({ count: 1 }),
      },
    } as never);
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response("email service unavailable", { status: 503 }),
    );
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    const response = await POST(
      new Request("http://localhost/api/billing/webhook", {
        method: "POST",
        headers: { "stripe-signature": "sig" },
        body: "{}",
      }),
    );

    expect(response.status).toBe(200);
    expect(deleteEvent).not.toHaveBeenCalled();
    expect(consoleError).toHaveBeenCalled();
    consoleError.mockRestore();
  });

  it("maps subscription updates to the Scale plan by Stripe price ID", async () => {
    const updateMany = vi.fn().mockResolvedValue({ count: 1 });
    const stripe = {
      webhooks: {
        constructEvent: vi.fn().mockReturnValue({
          id: "evt_4",
          type: "customer.subscription.updated",
          data: {
            object: {
              id: "sub_123",
              customer: "cus_123",
              status: "active",
              trial_end: null,
              items: {
                data: [
                  {
                    price: { id: "price_scale" },
                    current_period_start: 1770000000,
                    current_period_end: 1772592000,
                  },
                ],
              },
            },
          },
        }),
      },
    };

    vi.mocked(getServerStripeClient).mockReturnValue(stripe as never);
    vi.mocked(getPrismaClient).mockReturnValue({
      stripeWebhookEvent: {
        create: vi.fn().mockResolvedValue({}),
        delete: vi.fn().mockResolvedValue({}),
      },
      user: {
        updateMany,
      },
    } as never);

    const response = await POST(
      new Request("http://localhost/api/billing/webhook", {
        method: "POST",
        headers: { "stripe-signature": "sig" },
        body: "{}",
      }),
    );

    expect(response.status).toBe(200);
    expect(updateMany).toHaveBeenCalledWith(
      expect.objectContaining({
        data: expect.objectContaining({
          plan: "scale",
          subscription_status: "active",
          stripe_subscription_id: "sub_123",
        }),
      }),
    );
  });
});
