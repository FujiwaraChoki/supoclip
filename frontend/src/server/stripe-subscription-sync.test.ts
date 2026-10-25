import {
  applyPaidUpgradeInvoice,
  getPaidSubscriptionPlan,
  noteUpgradePaymentFailed,
  restorePastDueEntitlement,
  syncSubscriptionUpdate,
} from "./stripe-subscription-sync";
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

  it("does not restore over an upgrade that is still waiting for payment", async () => {
    vi.mocked(getPrismaClient).mockReturnValue({
      user: {
        findUnique: vi.fn().mockResolvedValue({
          plan: "free",
          subscription_status: "past_due",
          subscription_provider: "stripe",
          stripe_subscription_id: "sub_123",
          pending_plan: "scale",
        }),
      },
    } as never);

    await expect(restorePastDueEntitlement("user-1")).resolves.toBe(false);
    expect(getServerStripeClient).not.toHaveBeenCalled();
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

describe("paid upgrades", () => {
  type BillingRow = {
    plan: string;
    subscription_status: string;
    subscription_provider: string | null;
    pending_plan: string | null;
    pending_plan_invoice_id: string | null;
  };

  function store(initial: Partial<BillingRow> & Pick<BillingRow, "plan">) {
    let row: BillingRow = {
      subscription_status: "active",
      subscription_provider: "stripe",
      pending_plan: null,
      pending_plan_invoice_id: null,
      ...initial,
    };
    const findFirst = vi.fn(async () => ({ ...row }));
    const updateMany = vi.fn(async ({ where, data }: { where?: Record<string, unknown>; data: Partial<BillingRow> }) => {
      if (typeof where?.plan === "string" && where.plan !== row.plan) return { count: 0 };
      const planIn = (where?.plan as { in?: string[] } | undefined)?.in;
      if (planIn && !planIn.includes(row.plan)) return { count: 0 };
      row = { ...row, ...data };
      return { count: 1 };
    });
    vi.mocked(getPrismaClient).mockReturnValue({
      user: { findFirst, updateMany },
    } as never);
    return { read: () => ({ ...row }) };
  }

  function subscription(
    status: Stripe.Subscription.Status,
    priceId: string,
    invoice: Stripe.Subscription["latest_invoice"] = null,
  ): Stripe.Subscription {
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
    } as Stripe.Subscription;
  }

  function invoice(status: Stripe.Invoice.Status, amountDue: number, id = "in_upgrade"): Stripe.Invoice {
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
          { amount: -400, pricing: { type: "price_details", unit_amount_decimal: "999", price_details: { price: "price_pro", product: "prod_pro" } } },
          { amount: Math.max(amountDue, 0), pricing: { type: "price_details", unit_amount_decimal: "2499", price_details: { price: "price_scale", product: "prod_scale" } } },
        ],
      },
    } as Stripe.Invoice;
  }

  beforeEach(() => {
    vi.resetAllMocks();
    process.env.STRIPE_PRO_PRICE_ID = "price_pro";
    process.env.STRIPE_SCALE_PRICE_ID = "price_scale";
    vi.mocked(getServerStripeClient).mockReturnValue({ invoices: { retrieve: vi.fn() } } as never);
  });

  it("does not move the billing window while the upgrade invoice is open", async () => {
    const updateMany = vi.fn().mockResolvedValue({ count: 1 });
    vi.mocked(getPrismaClient).mockReturnValue({
      user: {
        findFirst: vi.fn().mockResolvedValue({
          plan: "pro",
          subscription_status: "active",
          subscription_provider: "stripe",
          pending_plan: null,
          pending_plan_invoice_id: null,
        }),
        updateMany,
      },
    } as never);

    await syncSubscriptionUpdate(subscription("active", "price_scale", invoice("open", 1500)));

    const data = updateMany.mock.calls[0][0].data as Record<string, unknown>;
    expect(data.plan).toBe("pro");
    expect(data.pending_plan).toBe("scale");
    expect(data).not.toHaveProperty("billing_period_start");
    expect(data).not.toHaveProperty("billing_period_end");
  });

  it("grants the higher tier when amount_due is 0", async () => {
    const rows = store({ plan: "pro" });
    await syncSubscriptionUpdate(subscription("active", "price_scale", invoice("open", 0)));
    expect(rows.read()).toMatchObject({ plan: "scale", pending_plan: null, subscription_status: "active" });
  });

  it("loads latest_invoice when the webhook only includes its id", async () => {
    const retrieve = vi.fn().mockResolvedValue(invoice("paid", 1500));
    vi.mocked(getServerStripeClient).mockReturnValue({ invoices: { retrieve } } as never);
    const rows = store({ plan: "pro" });

    await syncSubscriptionUpdate(subscription("active", "price_scale", "in_upgrade"));

    expect(retrieve).toHaveBeenCalledWith("in_upgrade");
    expect(rows.read().plan).toBe("scale");
  });

  it("keeps a same-tier plan while a renewal is past due", async () => {
    const rows = store({ plan: "pro", pending_plan: "scale", pending_plan_invoice_id: "in_old" });
    await syncSubscriptionUpdate(subscription("past_due", "price_pro"));
    expect(rows.read()).toMatchObject({
      plan: "pro",
      subscription_status: "past_due",
      pending_plan: null,
      pending_plan_invoice_id: null,
    });
  });

  it("does not let a stale unpaid snapshot overwrite a tier that was already granted", async () => {
    let row: BillingRow = {
      plan: "scale",
      subscription_status: "active",
      subscription_provider: "stripe",
      pending_plan: null,
      pending_plan_invoice_id: null,
    };
    vi.mocked(getPrismaClient).mockReturnValue({
      user: {
        findFirst: vi.fn(async () => ({
          plan: "pro",
          subscription_status: "active",
          subscription_provider: "stripe",
          pending_plan: null,
          pending_plan_invoice_id: null,
        })),
        updateMany: vi.fn(async ({ where, data }: { where?: { plan?: string }; data: Partial<BillingRow> }) => {
          if (where?.plan && where.plan !== row.plan) return { count: 0 };
          row = { ...row, ...data };
          return { count: 1 };
        }),
      },
    } as never);

    await syncSubscriptionUpdate(subscription("active", "price_scale", invoice("open", 1500)));

    expect(row.plan).toBe("scale");
    expect(row.pending_plan).toBeNull();
  });

  it("does not revoke a tier that was already paid when a later charge fails", async () => {
    const rows = store({ plan: "scale" });
    await noteUpgradePaymentFailed(invoice("open", 1500));
    expect(rows.read().plan).toBe("scale");
    expect(rows.read().pending_plan).toBeNull();
  });

  it("grants a pending higher tier from invoice.payment_succeeded", async () => {
    const rows = store({ plan: "pro", pending_plan: "scale", pending_plan_invoice_id: "in_upgrade" });
    await applyPaidUpgradeInvoice(invoice("paid", 1500));
    expect(rows.read()).toMatchObject({ plan: "scale", pending_plan: null, pending_plan_invoice_id: null });
  });

  it("grants the pending tier when the paid invoice id matches and lines omit the price", async () => {
    const rows = store({ plan: "pro", pending_plan: "scale", pending_plan_invoice_id: "in_upgrade" });
    const paid = invoice("paid", 1500);
    paid.lines = { data: [] } as unknown as Stripe.Invoice["lines"];

    await applyPaidUpgradeInvoice(paid);

    expect(rows.read()).toMatchObject({ plan: "scale", pending_plan: null, pending_plan_invoice_id: null });
  });

  it("does not treat an unrelated paid invoice as a pending upgrade with no stored invoice id", async () => {
    const rows = store({ plan: "pro", pending_plan: "scale", pending_plan_invoice_id: null });
    const renewal = invoice("paid", 999, "in_renewal");
    renewal.lines = {
      data: [
        {
          amount: 999,
          pricing: {
            type: "price_details",
            unit_amount_decimal: "999",
            price_details: { price: "price_pro", product: "prod_pro" },
          },
        },
      ],
    } as Stripe.Invoice["lines"];

    await applyPaidUpgradeInvoice(renewal);

    expect(rows.read()).toMatchObject({
      plan: "pro",
      pending_plan: "scale",
      pending_plan_invoice_id: null,
    });
  });

  it("leaves a pending upgrade in place when a different invoice is paid", async () => {
    const rows = store({ plan: "pro", pending_plan: "scale", pending_plan_invoice_id: "in_upgrade" });
    const renewal = invoice("paid", 999, "in_renewal");
    renewal.lines = {
      data: [
        {
          amount: 999,
          pricing: {
            type: "price_details",
            unit_amount_decimal: "999",
            price_details: { price: "price_pro", product: "prod_pro" },
          },
        },
      ],
    } as Stripe.Invoice["lines"];

    await applyPaidUpgradeInvoice(renewal);

    expect(rows.read()).toMatchObject({
      plan: "pro",
      pending_plan: "scale",
      pending_plan_invoice_id: "in_upgrade",
    });
  });

  it("keeps an affiliate plan until the upgrade invoice is paid", async () => {
    const rows = store({ plan: "pro", subscription_provider: "affiliate" });
    await syncSubscriptionUpdate(subscription("active", "price_scale", invoice("open", 1500)));
    expect(rows.read()).toMatchObject({
      plan: "pro",
      subscription_provider: "affiliate",
      pending_plan: null,
    });

    await applyPaidUpgradeInvoice(invoice("paid", 1500));
    expect(rows.read()).toMatchObject({
      plan: "scale",
      subscription_provider: "stripe",
      pending_plan: null,
    });
  });
});
