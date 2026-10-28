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
      STRIPE_PRO_ANNUAL_PRICE_ID: "price_pro_annual",
      STRIPE_PRO_LEGACY_PRICE_IDS: "price_pro_2025, price_pro_2026_03",
    };
  });

  async function sendSubscriptionEvent(status: string, priceId: string) {
    const updateMany = vi.fn().mockResolvedValue({ count: 1 });
    vi.mocked(getServerStripeClient).mockReturnValue({
      webhooks: {
        constructEvent: vi.fn().mockReturnValue({
          id: `evt_${status}_${priceId}`,
          type: "customer.subscription.updated",
          data: {
            object: {
              id: "sub_123",
              customer: "cus_123",
              status,
              trial_end: null,
              items: {
                data: [{ price: { id: priceId }, current_period_start: 1770000000, current_period_end: 1801536000 }],
              },
            },
          },
        }),
      },
    } as never);
    vi.mocked(getPrismaClient).mockReturnValue({
      stripeWebhookEvent: { create: vi.fn().mockResolvedValue({}), delete: vi.fn().mockResolvedValue({}) },
      user: { updateMany, findFirst: vi.fn().mockResolvedValue(null) },
    } as never);
    const response = await POST(
      new Request("http://localhost/api/billing/webhook", {
        method: "POST",
        headers: { "stripe-signature": "sig" },
        body: "{}",
      }),
    );
    expect(response.status).toBe(200);
    return updateMany.mock.calls[0][0].data as Record<string, unknown>;
  }

  it("maps an annual price to its plan", async () => {
    const data = await sendSubscriptionEvent("active", "price_pro_annual");
    expect(data.plan).toBe("pro");
    expect(data).not.toHaveProperty("legacy_hd");
  });

  it("ends grandfathered 1080p when the subscription is cancelled", async () => {
    const data = await sendSubscriptionEvent("canceled", "price_pro");
    expect(data.plan).toBe("free");
    expect(data.legacy_hd).toBe(false);
  });

  it("keeps subscribers on an earlier Pro price on Pro, with 1080p", async () => {
    const data = await sendSubscriptionEvent("active", "price_pro_2026_03");
    expect(data.plan).toBe("pro");
    expect(data.legacy_hd).toBe(true);
  });

  it("does not grandfather the current Pro price", async () => {
    const data = await sendSubscriptionEvent("active", "price_pro");
    expect(data).not.toHaveProperty("legacy_hd");
  });

  it("keeps grandfathered 1080p through a failed payment", async () => {
    const data = await sendSubscriptionEvent("past_due", "price_pro");
    expect(data.plan).toBe("pro");
    expect(data.subscription_status).toBe("past_due");
    expect(data).not.toHaveProperty("legacy_hd");
  });

  it("keeps the paid plan and 1080p while Stripe retries a grandfathered Pro invoice", async () => {
    const data = await sendSubscriptionEvent("past_due", "price_pro_2026_03");
    expect(data.plan).toBe("pro");
    expect(data.subscription_status).toBe("past_due");
    expect(data.subscription_provider).toBe("stripe");
    expect(data.legacy_hd).toBe(true);
  });

  it("drops the paid plan once Stripe stops retrying", async () => {
    const data = await sendSubscriptionEvent("unpaid", "price_scale");
    expect(data.plan).toBe("free");
    expect(data.subscription_status).toBe("unpaid");
    expect(data.subscription_provider).toBe("stripe");
    expect(data).not.toHaveProperty("legacy_hd");
  });

  it("does not grant a plan before the first invoice is paid", async () => {
    const data = await sendSubscriptionEvent("incomplete", "price_pro");
    expect(data.plan).toBe("free");
    expect(data.subscription_status).toBe("incomplete");
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
    const updateMany = vi.fn().mockResolvedValue({ count: 1 });
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
      affiliate: { findUnique: vi.fn().mockResolvedValue(null) },
      user: {
        findFirst: vi.fn().mockResolvedValue({ id: "user-1", subscription_provider: "stripe" }),
        updateMany,
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
    expect(updateMany).toHaveBeenCalledWith(
      expect.objectContaining({
        where: {
          stripe_customer_id: "cus_123",
          OR: [{ subscription_provider: "stripe" }, { subscription_provider: null }],
        },
        data: expect.objectContaining({
          plan: "free",
          subscription_status: "canceled",
          subscription_provider: null,
        }),
      }),
    );
    expect(response.status).toBe(200);
    expect(deleteEvent).not.toHaveBeenCalled();
  });

  it("returns an approved creator to their free Pro when their paid plan is deleted", async () => {
    const updateMany = vi.fn().mockResolvedValue({ count: 1 });
    vi.mocked(getServerStripeClient).mockReturnValue({
      webhooks: {
        constructEvent: vi.fn().mockReturnValue({
          id: "evt_creator",
          type: "customer.subscription.deleted",
          data: { object: { customer: "cus_123" } },
        }),
      },
    } as never);
    vi.mocked(getPrismaClient).mockReturnValue({
      stripeWebhookEvent: { create: vi.fn().mockResolvedValue({}), delete: vi.fn() },
      affiliate: { findUnique: vi.fn().mockResolvedValue({ status: "approved" }) },
      user: {
        findFirst: vi.fn().mockResolvedValue({ id: "user-1", subscription_provider: "stripe" }),
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
    expect(updateMany).toHaveBeenLastCalledWith(
      expect.objectContaining({
        data: { plan: "pro", subscription_status: "active", subscription_provider: "affiliate" },
      }),
    );
    expect(fetchBackend).not.toHaveBeenCalled();
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
      affiliate: { findUnique: vi.fn().mockResolvedValue(null) },
      user: {
        findFirst: vi.fn().mockResolvedValue({ id: "user-1", subscription_provider: "stripe" }),
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

  it("does not send an unsubscribe email when Stripe deletion is guarded by Apple provider", async () => {
    const stripe = {
      webhooks: {
        constructEvent: vi.fn().mockReturnValue({
          id: "evt_apple_guard",
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
        delete: vi.fn().mockResolvedValue({}),
      },
      user: {
        findFirst: vi.fn().mockResolvedValue({ id: "user-1", subscription_provider: "apple" }),
        updateMany: vi.fn().mockResolvedValue({ count: 0 }),
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
    expect(fetchBackend).not.toHaveBeenCalled();
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
        findFirst: vi.fn().mockResolvedValue(null),
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
          subscription_provider: "stripe",
          stripe_subscription_id: "sub_123",
        }),
      }),
    );
  });

  it("stores the scheduled cancellation date from a subscription update", async () => {
    const updateMany = vi.fn().mockResolvedValue({ count: 1 });
    const stripe = {
      webhooks: {
        constructEvent: vi.fn().mockReturnValue({
          id: "evt_cancel",
          type: "customer.subscription.updated",
          data: {
            object: {
              id: "sub_123",
              customer: "cus_123",
              status: "active",
              trial_end: null,
              cancel_at: null,
              cancel_at_period_end: true,
              items: {
                data: [
                  {
                    price: { id: "price_pro" },
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
        findFirst: vi.fn().mockResolvedValue(null),
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
          plan: "pro",
          subscription_cancel_at: new Date(1772592000 * 1000),
        }),
      }),
    );
  });

  describe("scheduled cancellation emails", () => {
    function mockCancellationUpdate(
      changes: Record<string, unknown> = {},
      previousAttributes: Record<string, unknown> = {
        cancel_at_period_end: false,
        cancel_at: null,
      },
    ) {
      const createEvent = vi.fn().mockResolvedValue({});
      const deleteEvent = vi.fn().mockResolvedValue({});
      const updateMany = vi.fn().mockResolvedValue({ count: 1 });
      const findFirst = vi.fn().mockResolvedValue({
        id: "user-1",
        plan: "pro",
        subscription_provider: "stripe",
        subscription_status: "active",
      });
      vi.mocked(getPrismaClient).mockReturnValue({
        stripeWebhookEvent: { create: createEvent, delete: deleteEvent },
        user: { updateMany, findFirst },
      } as never);
      const subscription = {
        id: "sub_123",
        customer: "cus_123",
        status: "active",
        cancel_at_period_end: true,
        cancel_at: 1772592000,
        trial_end: null,
        items: {
          data: [{
            price: { id: "price_pro" },
            current_period_start: 1770000000,
            current_period_end: 1772592000,
          }],
        },
        ...changes,
      };
      const retrieveSubscription = vi.fn().mockResolvedValue(subscription);
      vi.mocked(getServerStripeClient).mockReturnValue({
        webhooks: {
          constructEvent: vi.fn().mockReturnValue({
            id: "evt_scheduled_cancellation",
            type: "customer.subscription.updated",
            data: { previous_attributes: previousAttributes, object: subscription },
          }),
        },
        subscriptions: { retrieve: retrieveSubscription },
      } as never);
      vi.mocked(fetchBackend).mockResolvedValue(new Response("{}", { status: 200 }));
      const deliver = () => POST(new Request("http://localhost/api/billing/webhook", {
        method: "POST",
        headers: { "stripe-signature": "sig" },
        body: "{}",
      }));
      return { deliver, createEvent, deleteEvent, updateMany, findFirst, retrieveSubscription };
    }

    it.each([
      { cancel_at_period_end: true, cancel_at: 1772592000 },
      { cancel_at_period_end: true, cancel_at: null },
      { cancel_at_period_end: false, cancel_at: 1772592000 },
    ])("emails immediately when cancellation is newly scheduled: %j", async (changes) => {
      const { deliver, updateMany, findFirst } = mockCancellationUpdate(changes);

      expect((await deliver()).status).toBe(200);

      expect(updateMany).toHaveBeenCalledWith(expect.objectContaining({
        data: expect.objectContaining({ plan: "pro", subscription_status: "active" }),
      }));
      expect(findFirst).toHaveBeenLastCalledWith({
        where: {
          stripe_customer_id: "cus_123",
          stripe_subscription_id: "sub_123",
          subscription_provider: "stripe",
          subscription_cancel_at: { not: null },
        },
        select: { id: true },
      });
      expect(fetchBackend).toHaveBeenCalledExactlyOnceWith("/billing/subscription-email", expect.objectContaining({
        userId: "user-1",
        body: JSON.stringify({ event: "cancellation_scheduled" }),
      }));
      expect(updateMany.mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(fetchBackend).mock.invocationCallOrder[0]);
    });

    it.each([
      { changes: { cancel_at_period_end: false, cancel_at: null }, previous: { cancel_at_period_end: true, cancel_at: 1772592000 } },
      { changes: {}, previous: { metadata: {} } },
      { changes: {}, previous: {} },
      { changes: {}, previous: { cancel_at: 1772500000 } },
      { changes: {}, previous: { cancel_at_period_end: false } },
      { changes: { status: "canceled" }, previous: { cancel_at_period_end: false, cancel_at: null } },
    ])("does not email for a resumed, existing, rescheduled, or ended cancellation: %j", async ({ changes, previous }) => {
      const { deliver } = mockCancellationUpdate(changes, previous);

      expect((await deliver()).status).toBe(200);
      expect(fetchBackend).not.toHaveBeenCalled();
    });

    it("does not email when the user no longer owns this Stripe subscription", async () => {
      const { deliver, findFirst } = mockCancellationUpdate();
      findFirst.mockResolvedValueOnce({ plan: "pro" }).mockResolvedValueOnce(null);

      expect((await deliver()).status).toBe(200);
      expect(fetchBackend).not.toHaveBeenCalled();
    });

    it("does not resend a successfully processed event", async () => {
      const { deliver, createEvent } = mockCancellationUpdate();

      expect((await deliver()).status).toBe(200);
      createEvent.mockRejectedValueOnce({ code: "P2002" });
      expect((await deliver()).status).toBe(200);
      expect(fetchBackend).toHaveBeenCalledTimes(1);
    });

    it("allows Stripe to retry if the email service fails after the state is synced", async () => {
      const { deliver, deleteEvent } = mockCancellationUpdate();
      vi.mocked(fetchBackend).mockResolvedValueOnce(new Response("SES unavailable", { status: 503 }));

      await expect(deliver()).rejects.toThrow("Backend subscription email failed with 503");
      expect(deleteEvent).toHaveBeenCalledWith({ where: { id: "evt_scheduled_cancellation" } });
      expect((await deliver()).status).toBe(200);
      expect(fetchBackend).toHaveBeenCalledTimes(2);
    });

    it("syncs the live subscription and skips the email when a retry arrives after a resume", async () => {
      const { deliver, updateMany, retrieveSubscription } = mockCancellationUpdate();
      retrieveSubscription.mockResolvedValueOnce({
        id: "sub_123",
        customer: "cus_123",
        status: "active",
        cancel_at_period_end: false,
        cancel_at: null,
        trial_end: null,
        items: {
          data: [{
            price: { id: "price_pro" },
            current_period_start: 1770000000,
            current_period_end: 1772592000,
          }],
        },
      });

      expect((await deliver()).status).toBe(200);
      expect(retrieveSubscription).toHaveBeenCalledWith("sub_123");
      expect(updateMany).toHaveBeenCalledWith(expect.objectContaining({
        data: expect.objectContaining({ subscription_cancel_at: null }),
      }));
      expect(fetchBackend).not.toHaveBeenCalled();
    });
  });

  it("scopes non-paid subscription updates so they cannot clobber an Apple entitlement", async () => {
    const updateMany = vi.fn().mockResolvedValue({ count: 0 });
    const stripe = {
      webhooks: {
        constructEvent: vi.fn().mockReturnValue({
          id: "evt_5",
          type: "customer.subscription.updated",
          data: {
            object: {
              id: "sub_123",
              customer: "cus_123",
              status: "canceled",
              trial_end: null,
              items: { data: [] },
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
        findFirst: vi.fn().mockResolvedValue(null),
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
        where: {
          stripe_customer_id: "cus_123",
          OR: [{ subscription_provider: "stripe" }, { subscription_provider: null }],
        },
      }),
    );
  });

  it("scopes active subscriptions with unmapped prices so they cannot clobber an Apple entitlement", async () => {
    const updateMany = vi.fn().mockResolvedValue({ count: 0 });
    const stripe = {
      webhooks: {
        constructEvent: vi.fn().mockReturnValue({
          id: "evt_unmapped_active",
          type: "customer.subscription.updated",
          data: {
            object: {
              id: "sub_unmapped",
              customer: "cus_123",
              status: "active",
              trial_end: null,
              items: {
                data: [
                  {
                    price: { id: "price_unknown" },
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
        findFirst: vi.fn().mockResolvedValue(null),
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
        where: {
          stripe_customer_id: "cus_123",
          OR: [{ subscription_provider: "stripe" }, { subscription_provider: null }],
        },
        data: expect.objectContaining({
          plan: "free",
          subscription_status: "active",
          subscription_provider: "stripe",
        }),
      }),
    );
  });
});

describe("/api/billing/webhook upgrade payment", () => {
  const env = process.env;

  beforeEach(() => {
    vi.resetAllMocks();
    process.env = {
      ...env,
      STRIPE_WEBHOOK_SECRET: "whsec_test",
      STRIPE_PRO_PRICE_ID: "price_pro",
      STRIPE_SCALE_PRICE_ID: "price_scale",
      STRIPE_PRO_ANNUAL_PRICE_ID: "price_pro_annual",
      STRIPE_SCALE_ANNUAL_PRICE_ID: "price_scale_annual",
    };
  });

  afterAll(() => {
    process.env = env;
  });

  type BillingRow = {
    plan: string;
    subscription_status: string;
    subscription_provider: string | null;
    pending_plan: string | null;
    pending_plan_invoice_id: string | null;
    stripe_subscription_id: string | null;
  };

  function billingRow(plan: string): BillingRow {
    return {
      plan,
      subscription_status: "active",
      subscription_provider: "stripe",
      pending_plan: null,
      pending_plan_invoice_id: null,
      stripe_subscription_id: "sub_123",
    };
  }

  function harness(initial: BillingRow) {
    let row: BillingRow = { ...initial };
    let sequence = 0;
    const findFirst = vi.fn(async () => ({ ...row }));
    const updateMany = vi.fn(async ({ where, data }: { where?: Record<string, unknown>; data: Partial<BillingRow> }) => {
      if (typeof where?.plan === "string" && where.plan !== row.plan) return { count: 0 };
      const planIn = (where?.plan as { in?: string[] } | undefined)?.in;
      if (planIn && !planIn.includes(row.plan)) return { count: 0 };
      if (
        typeof where?.pending_plan_invoice_id === "string" &&
        where.pending_plan_invoice_id !== row.pending_plan_invoice_id
      ) {
        return { count: 0 };
      }
      const providers = where?.OR as Array<{ subscription_provider: string | null }> | undefined;
      if (providers && !providers.some((entry) => entry.subscription_provider === row.subscription_provider)) {
        return { count: 0 };
      }
      row = { ...row, ...data };
      return { count: 1 };
    });

    async function post(type: string, object: Record<string, unknown>) {
      sequence += 1;
      vi.mocked(getServerStripeClient).mockReturnValue({
        webhooks: {
          constructEvent: vi.fn().mockReturnValue({
            id: `evt_upgrade_${sequence}`,
            type,
            data: { object },
          }),
        },
        invoices: { retrieve: vi.fn() },
      } as never);
      vi.mocked(getPrismaClient).mockReturnValue({
        stripeWebhookEvent: {
          create: vi.fn().mockResolvedValue({}),
          delete: vi.fn().mockResolvedValue({}),
        },
        user: { findFirst, updateMany },
      } as never);
      const response = await POST(
        new Request("http://localhost/api/billing/webhook", {
          method: "POST",
          headers: { "stripe-signature": "sig" },
          body: "{}",
        }),
      );
      expect(response.status).toBe(200);
      return { ...row };
    }

    return { post, read: () => ({ ...row }) };
  }

  function subscription(priceId: string, status: string, invoice: Record<string, unknown> | null) {
    return {
      id: "sub_123",
      customer: "cus_123",
      status,
      trial_end: null,
      cancel_at: null,
      cancel_at_period_end: false,
      latest_invoice: invoice,
      items: {
        data: [
          {
            price: { id: priceId },
            current_period_start: 1770000000,
            current_period_end: 1772592000,
          },
        ],
      },
    };
  }

  function upgradeInvoice(status: string, amountDue: number, id = "in_upgrade") {
    return {
      id,
      customer: "cus_123",
      status,
      amount_due: amountDue,
      parent: {
        type: "subscription_details",
        quote_details: null,
        subscription_details: { subscription: "sub_123", metadata: null },
      },
      lines: {
        data: [
          {
            amount: -400,
            pricing: { type: "price_details", unit_amount_decimal: "999", price_details: { price: "price_pro", product: "prod_pro" } },
          },
          {
            amount: amountDue,
            pricing: { type: "price_details", unit_amount_decimal: "2499", price_details: { price: "price_scale", product: "prod_scale" } },
          },
        ],
      },
    };
  }

  it("grants the higher tier when the upgrade invoice is already paid", async () => {
    const { post } = harness(billingRow("pro"));
    const paid = upgradeInvoice("paid", 1500);

    const row = await post(
      "customer.subscription.updated",
      subscription("price_scale", "active", paid),
    );

    expect(row.plan).toBe("scale");
    expect(row.pending_plan).toBeNull();
    expect(row.subscription_status).toBe("active");
  });

  it("keeps the paid tier until invoice.paid when the upgrade invoice is open", async () => {
    const { post } = harness(billingRow("pro"));
    const open = upgradeInvoice("open", 1500);

    const held = await post(
      "customer.subscription.updated",
      subscription("price_scale", "active", open),
    );
    expect(held.plan).toBe("pro");
    expect(held.pending_plan).toBe("scale");
    expect(held.pending_plan_invoice_id).toBe("in_upgrade");

    const granted = await post("invoice.paid", { ...open, status: "paid", amount_due: 0 });
    expect(granted.plan).toBe("scale");
    expect(granted.pending_plan).toBeNull();
    expect(granted.pending_plan_invoice_id).toBeNull();
    expect(granted.subscription_status).toBe("active");
  });

  it("keeps the paid tier when the upgrade payment fails", async () => {
    const { post } = harness(billingRow("pro"));
    const open = upgradeInvoice("open", 1500);

    await post("customer.subscription.updated", subscription("price_scale", "active", open));
    const failed = await post("invoice.payment_failed", open);
    expect(failed.plan).toBe("pro");
    expect(failed.pending_plan).toBe("scale");

    const pastDue = await post(
      "customer.subscription.updated",
      subscription("price_scale", "past_due", open),
    );
    expect(pastDue.plan).toBe("pro");
    expect(pastDue.subscription_status).toBe("past_due");

    const unpaid = await post(
      "customer.subscription.updated",
      subscription("price_scale", "unpaid", open),
    );
    expect(unpaid.plan).toBe("pro");
    expect(unpaid.plan).not.toBe("scale");
    expect(unpaid.subscription_status).toBe("past_due");
  });

  it("applies a downgrade on subscription.updated", async () => {
    const { post } = harness(billingRow("scale"));

    const row = await post(
      "customer.subscription.updated",
      subscription("price_pro", "active", null),
    );

    expect(row.plan).toBe("pro");
    expect(row.pending_plan).toBeNull();
    expect(row.subscription_status).toBe("active");
  });

  it("grants a higher tier when the paid invoice arrives first and ignores duplicate events", async () => {
    const { post } = harness(billingRow("pro"));
    const paid = upgradeInvoice("paid", 0, "in_upgrade");

    const granted = await post("invoice.payment_succeeded", paid);
    expect(granted.plan).toBe("scale");

    const duplicatePaid = await post("invoice.paid", paid);
    expect(duplicatePaid.plan).toBe("scale");
    expect(duplicatePaid.pending_plan).toBeNull();

    const staleUpdate = await post(
      "customer.subscription.updated",
      subscription("price_scale", "active", upgradeInvoice("open", 1500)),
    );
    expect(staleUpdate.plan).toBe("scale");
    expect(staleUpdate.pending_plan).toBeNull();

    const duplicateUpdate = await post(
      "customer.subscription.updated",
      subscription("price_scale", "active", upgradeInvoice("open", 1500)),
    );
    expect(duplicateUpdate.plan).toBe("scale");
  });
});
