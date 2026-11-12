import type Stripe from "stripe";

import { createAccountWebhookHandler, type AccountWebhookDependencies } from "@/server/stripe-account-webhook";
import { getPostHogServer } from "@/server/posthog";
import { getPrismaClient } from "@/server/prisma";
import { guardedBillingUpdate } from "@/server/stripe-migration-store";

import { handleAccountBillingWebhook, POST } from "./stripe-billing-webhook";

// The real account handler (signatures, inbox lease, migration engine) has its own
// tests. Here it only decides whether the event is dispatched and what it answers.
vi.mock("@/server/stripe-account-webhook", () => ({ createAccountWebhookHandler: vi.fn() }));
vi.mock("@/server/stripe-webhook-inbox", () => ({
  createPrismaWebhookInbox: () => ({ claim: vi.fn(), finish: vi.fn() }),
}));
vi.mock("@/server/stripe-migration-store", () => ({
  createPrismaMigrationStore: vi.fn(),
  bindAccountCheckout: vi.fn(),
  guardedBillingUpdate: vi.fn(),
}));
vi.mock("./stripe-billing-risk", () => ({ handleOwnedBillingRisk: vi.fn() }));
vi.mock("@/server/prisma", () => ({ getPrismaClient: vi.fn() }));
vi.mock("@/server/stripe", () => ({ getServerStripeClient: vi.fn() }));
vi.mock("@/server/backend-api", () => ({ fetchBackend: vi.fn() }));
vi.mock("@/server/posthog", () => ({ getPostHogServer: vi.fn() }));

type Outcome = "processed" | "failed" | "duplicate";

describe("account webhook analytics", () => {
  const env = process.env;
  const capture = vi.fn();
  let outcome: Outcome;
  let event: Stripe.Event;
  const handledAccounts: string[] = [];

  beforeEach(() => {
    vi.resetAllMocks();
    handledAccounts.length = 0;
    process.env = {
      ...env,
      STRIPE_ACCOUNT_MIGRATION_ENABLED: "true",
      STRIPE_SOURCE_ACCOUNT_ID: "acct_source",
      STRIPE_DESTINATION_ACCOUNT_ID: "acct_destination",
      STRIPE_PRO_PRICE_ID: "price_pro",
    };
    vi.mocked(getPostHogServer).mockReturnValue({ capture } as never);
    vi.mocked(createAccountWebhookHandler).mockImplementation(
      (deps: AccountWebhookDependencies) => async (_request: Request, accountId: string) => {
        handledAccounts.push(accountId);
        if (outcome === "duplicate") return Response.json({ received: true });
        await deps.dispatch(event);
        return outcome === "processed"
          ? Response.json({ received: true })
          : Response.json({ error: "retry" }, { status: 503 });
      },
    );
  });

  afterAll(() => {
    process.env = env;
  });

  function mockDb(claimed = new Set<string>()) {
    const create = vi.fn(async ({ data }: { data: { id: string; status?: string } }) => {
      if (claimed.has(data.id)) throw { code: "P2002" };
      claimed.add(data.id);
      return {};
    });
    vi.mocked(getPrismaClient).mockReturnValue({
      stripeWebhookEvent: { create },
      affiliate: { findUnique: vi.fn().mockResolvedValue(null) },
      user: {
        findFirst: vi.fn().mockResolvedValue({ id: "user-1", plan: "pro", subscription_provider: "stripe" }),
        updateMany: vi.fn().mockResolvedValue({ count: 0 }),
      },
    } as never);
    return { create, claimed };
  }

  const deleted = {
    id: "evt_deleted",
    type: "customer.subscription.deleted",
    created: 1793491200,
    livemode: true,
    data: {
      object: {
        id: "sub_1",
        customer: "cus_1",
        status: "canceled",
        items: { data: [{ price: { id: "price_pro", recurring: { interval: "month" } } }] },
      },
    },
  } as unknown as Stripe.Event;

  const request = () => new Request("http://localhost/api/billing/webhook/acct_destination", { method: "POST" });

  it("sends the event once the account route has processed it", async () => {
    mockDb();
    vi.mocked(guardedBillingUpdate).mockResolvedValue({ count: 1 } as never);
    outcome = "processed";
    event = deleted;

    expect((await handleAccountBillingWebhook(request(), "acct_destination")).status).toBe(200);
    expect(capture).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      distinctId: "user-1",
      event: "subscription_canceled",
      timestamp: new Date(1793491200 * 1000),
      properties: expect.objectContaining({ plan: "pro", interval: "month", provider: "stripe" }),
    }));
  });

  it("sends nothing when the account route fails or the event is a duplicate", async () => {
    mockDb();
    vi.mocked(guardedBillingUpdate).mockResolvedValue({ count: 1 } as never);
    event = deleted;

    outcome = "failed";
    expect((await handleAccountBillingWebhook(request(), "acct_destination")).status).toBe(503);
    outcome = "duplicate";
    expect((await handleAccountBillingWebhook(request(), "acct_destination")).status).toBe(200);
    expect(capture).not.toHaveBeenCalled();
  });

  it("routes the legacy root to the source account and claims a renewal once", async () => {
    const { claimed } = mockDb();
    outcome = "processed";
    const invoice = {
      id: "in_1",
      customer: "cus_1",
      status: "paid",
      billing_reason: "subscription_cycle",
      lines: { data: [{ amount: 999, period: { start: 1770000000, end: 1772592000 } }] },
    };

    event = { id: "evt_paid", type: "invoice.paid", created: 1, livemode: true, data: { object: invoice } } as unknown as Stripe.Event;
    expect((await POST(new Request("http://localhost/api/billing/webhook", { method: "POST" }))).status).toBe(200);
    event = { ...event, id: "evt_succeeded", type: "invoice.payment_succeeded" } as Stripe.Event;
    expect((await handleAccountBillingWebhook(request(), "acct_source")).status).toBe(200);

    expect(handledAccounts).toEqual(["acct_source", "acct_source"]);
    expect(capture).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ event: "subscription_renewed" }));
    expect([...claimed]).toEqual(["posthog:subscription_renewed:in_1"]);
  });
});
