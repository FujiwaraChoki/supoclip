import { dispatchBillingEvent } from "../src/server/stripe-billing-webhook";
import { getPrismaClient } from "../src/server/prisma";
import { afterAll, beforeEach, describe, expect, test, vi } from "vitest";
import Stripe from "stripe";
import { PrismaClient } from "../src/generated/prisma";
import {
  createPrismaMigrationStore,
  guardedBillingUpdate,
  migrationRecord,
} from "../src/server/stripe-migration-store";
import { createPrismaWebhookInbox } from "../src/server/stripe-webhook-inbox";
import { createAccountWebhookHandler } from "../src/server/stripe-account-webhook";
import { withUserBillingAccount } from "../src/server/stripe-account-routing";
import {
  runWithStripeAccount,
  stripeAccountContext,
} from "../src/lib/stripe-accounts";
import { syncSubscriptionUpdate } from "../src/server/stripe-subscription-sync";

const address = process.env.TEST_MIGRATION_DATABASE_URL;
if (
  !address ||
  !/^postgresql:\/\/stripe_test:.*@127\.0\.0\.1:\d+\/stripe_wiring$/.test(
    address,
  )
) {
  throw new Error(
    "Integration tests require the dedicated local stripe_wiring Postgres database",
  );
}
const db = new PrismaClient({ datasources: { db: { url: address } } });
const source = "acct_source";
const destination = "acct_destination";
const customer = "cus_unchanged";
const paidThrough = Math.floor(Date.now() / 1000) - 60;
const clients = new Map<string, Stripe>();
const objects = new Map<string, Stripe.Subscription>();
const invoices = new Map<string, Stripe.Invoice>();
const schedules = new Map<string, Stripe.SubscriptionSchedule>();
const dispatched = vi.fn(async (_event: Stripe.Event) => {});
let failCancel = false;

function client(account: string): Stripe {
  if (clients.has(account)) return clients.get(account)!;
  const sdk = new Stripe("sk_test_fixture", {
    httpClient: Stripe.createFetchHttpClient(async () => {
      throw new Error("External network forbidden in integration tests");
    }),
  });
  vi.spyOn(sdk.subscriptions, "retrieve").mockImplementation(async (id) => {
    const value = objects.get(account + ":" + id);
    if (!value) throw new Error("No subscription in this account");
    return structuredClone(value) as Stripe.Response<Stripe.Subscription>;
  });
  vi.spyOn(sdk.invoices, "retrieve").mockImplementation(async (id) => {
    const value = invoices.get(account + ":" + id);
    if (!value) throw new Error("No invoice in this account");
    return structuredClone(value) as Stripe.Response<Stripe.Invoice>;
  });
  vi.spyOn(sdk.subscriptionSchedules, "retrieve").mockImplementation(
    async (id) =>
      structuredClone(
        schedules.get(account + ":" + id),
      ) as Stripe.Response<Stripe.SubscriptionSchedule>,
  );
  vi.spyOn(sdk.subscriptionSchedules, "cancel").mockImplementation(
    async (id) => {
      if (failCancel) throw new Error("Simulated retryable Stripe outage");
      const value = schedules.get(account + ":" + id)!;
      value.status = "canceled";
      return structuredClone(
        value,
      ) as Stripe.Response<Stripe.SubscriptionSchedule>;
    },
  );
  vi.spyOn(sdk.subscriptions, "update").mockImplementation(async (id) => {
    const value = objects.get(account + ":" + id)!;
    value.cancel_at_period_end = true;
    return structuredClone(value) as Stripe.Response<Stripe.Subscription>;
  });
  vi.spyOn(sdk.charges, "retrieve").mockImplementation(async (id) => ({
    id, customer, refunded: true, metadata: {}, payment_intent: "pi_original",
  }) as Stripe.Response<Stripe.Charge>);
  vi.spyOn(sdk.invoicePayments, "list").mockResolvedValue({
    data: [{ invoice: "in_source_original" }], has_more: false,
  } as Stripe.Response<Stripe.ApiList<Stripe.InvoicePayment>>);
  clients.set(account, sdk);
  return sdk;
}

function subscription(
  account: string,
  id: string,
  price: string,
  starts: number,
): Stripe.Subscription {
  // Deliberately minimal Stripe fixtures at the transport boundary, never database mocks.
  const value = {
    id,
    object: "subscription",
    customer,
    status: "active",
    livemode: false,
    metadata: {},
    cancel_at: null,
    cancel_at_period_end: account === source,
    schedule: account === destination ? "sub_sched_import" : null,
    items: {
      has_more: false,
      data: [
        {
          id: "si_test",
          current_period_start: starts,
          current_period_end: starts + 2592000,
          price: { id: price, recurring: { interval: "month" } },
          quantity: 1,
        },
      ],
    },
  } as Stripe.Subscription;
  objects.set(account + ":" + id, value);
  return value;
}

async function seed(provider = "stripe") {
  await db.user.create({
    data: {
      id: "user_test",
      name: "Synthetic",
      email: "synthetic@example.invalid",
      stripe_customer_id: customer,
      stripe_subscription_id: "sub_source",
      plan: "pro",
      subscription_status: "active",
      subscription_provider: provider,
      legacy_hd: true,
      billing_period_end: new Date(paidThrough * 1000),
    },
  });
  await db.stripeAccountMigration.create({
    data: {
      id: "migration_test",
      user_id: "user_test",
      source_account: source,
      source_customer: customer,
      source_subscription: "sub_source",
      destination_account: destination,
      destination_customer: customer,
      destination_schedule: "sub_sched_import",
      stage: "destination_staged",
      source_stopped_verified: true,
      paid_through: new Date(paidThrough * 1000),
      livemode: false,
    },
  });
  subscription(source, "sub_source", "price_source", paidThrough - 2592000);
  subscription(
    destination,
    "sub_destination",
    "price_destination",
    paidThrough,
  );
  schedules.set(destination + ":sub_sched_import", {
    id: "sub_sched_import",
    customer,
    status: "not_started",
  } as Stripe.SubscriptionSchedule);
  invoices.set(source + ":in_source_original", { id: "in_source_original", customer, parent: { subscription_details: { subscription: "sub_source" } } } as Stripe.Invoice);
  invoices.set(destination + ":in_paid", {
    id: "in_paid",
    customer,
    status: "paid",
    amount_remaining: 0,
    parent: { subscription_details: { subscription: "sub_destination" } },
  } as Stripe.Invoice);
}

function handler(reconcileCurrent = false) {
  const inbox = createPrismaWebhookInbox(db);
  return createAccountWebhookHandler({
    store: createPrismaMigrationStore(db),
    client: async (id) => client(id),
    signatureClient: client,
    secret: (id) => "whsec_" + id,
    assertMode: (_id, event) => {
      if (event.livemode) throw new Error("Test mode only");
    },
    claim: inbox.claim,
    finish: inbox.finish,
    dispatch: dispatched,
    ...(reconcileCurrent
      ? {
          dispatchCurrent: async (
            _event: Stripe.Event,
            sub: Stripe.Subscription,
          ) => {
            await syncSubscriptionUpdate(sub);
          },
        }
      : {}),
  });
}

async function deliver(
  account: string,
  id: string,
  type: string,
  object: unknown,
  secretAccount = account,
  previous?: unknown,
  reconcileCurrent = false,
) {
  const payload = JSON.stringify({
    id,
    object: "event",
    type,
    livemode: false,
    data: { object, ...(previous ? { previous_attributes: previous } : {}) },
  });
  const signature = await client(
    account,
  ).webhooks.generateTestHeaderStringAsync({
    payload,
    secret: "whsec_" + secretAccount,
  });
  return handler(reconcileCurrent)(
    new Request("http://localhost/webhook/" + account, {
      method: "POST",
      body: payload,
      headers: { "stripe-signature": signature },
    }),
    account,
  );
}

beforeEach(async () => {
  process.env.STRIPE_ACCOUNT_MIGRATION_ENABLED = "true";
  process.env.STRIPE_SOURCE_ACCOUNT_ID = source;
  process.env.STRIPE_DESTINATION_ACCOUNT_ID = destination;
  process.env.STRIPE_PRO_PRICE_ID = "price_source";
  process.env.STRIPE_DESTINATION_PRO_PRICE_ID = "price_destination";
  process.env.STRIPE_DESTINATION_SCALE_PRICE_ID = "price_destination_scale";
  await db.stripeWebhookEvent.deleteMany();
  await db.stripeAccountMigration.deleteMany();
  await db.user.deleteMany();
  objects.clear();
  invoices.clear();
  schedules.clear();
  dispatched.mockClear();
  failCancel = false;
});
afterAll(async () => {
  await db.$disconnect();
  await getPrismaClient().$disconnect();
});

describe("signed webhooks and real Postgres ownership transactions", () => {
  test("first-renewal authentication routes to payment recovery without activating", async () => {
    await seed();
    objects.get(destination + ":sub_destination")!.status = "incomplete";
    expect(
      (
        await deliver(
          destination,
          "evt_sca",
          "invoice.payment_action_required",
          {
            id: "in_action",
            customer,
            parent: {
              subscription_details: { subscription: "sub_destination" },
            },
          },
        )
      ).status,
    ).toBe(200);
    expect(
      (
        await db.stripeAccountMigration.findUniqueOrThrow({
          where: { id: "migration_test" },
        })
      ).recovery_required,
    ).toBe(true);
    expect(
      (await db.user.findUniqueOrThrow({ where: { id: "user_test" } }))
        .stripe_subscription_id,
    ).toBe("sub_source");
  });
  test("a source refund holds the import and stops its staged replacement without revoking another grant", async () => {
    await seed("apple");
    expect(
      (
        await deliver(source, "evt_refund", "charge.refunded", {
          id: "ch_original",
          customer,
          refunded: true,
        })
      ).status,
    ).toBe(200);
    expect(
      (
        await db.stripeAccountMigration.findUniqueOrThrow({
          where: { id: "migration_test" },
        })
      ).review_reason,
    ).toMatch(/Refund or dispute/);
    expect(schedules.get(destination + ":sub_sched_import")!.status).toBe(
      "canceled",
    );
    expect(
      (await db.user.findUniqueOrThrow({ where: { id: "user_test" } }))
        .subscription_provider,
    ).toBe("apple");
  });
  test("source upgrades before staging still use the existing paid-invoice policy", async () => {
    await seed();
    await db.stripeAccountMigration.update({
      where: { id: "migration_test" },
      data: { stage: "prepared" },
    });
    process.env.STRIPE_SCALE_PRICE_ID = "price_source_scale";
    const sub = objects.get(source + ":sub_source")!;
    sub.cancel_at_period_end = false;
    sub.items.data[0].price.id = "price_source_scale";
    sub.latest_invoice = "in_source_upgrade";
    invoices.set(source + ":in_source_upgrade", {
      id: "in_source_upgrade",
      status: "open",
      amount_due: 1000,
    } as Stripe.Invoice);
    expect(
      (
        await deliver(
          source,
          "evt_source_upgrade",
          "customer.subscription.updated",
          { id: sub.id, customer },
          source,
          undefined,
          true,
        )
      ).status,
    ).toBe(200);
    expect(
      await db.user.findUniqueOrThrow({ where: { id: "user_test" } }),
    ).toMatchObject({ plan: "pro", pending_plan: "scale" });
  });
  test("current destination webhooks use the real tier/paid-upgrade policy and recover on payment", async () => {
    await seed();
    const paid = {
      id: "in_paid",
      customer,
      parent: { subscription_details: { subscription: "sub_destination" } },
    };
    expect(
      (await deliver(destination, "evt_activate", "invoice.paid", paid)).status,
    ).toBe(200);
    const sub = objects.get(destination + ":sub_destination")!;
    sub.items.data[0].price.id = "price_destination_scale";
    sub.latest_invoice = "in_upgrade";
    invoices.set(destination + ":in_upgrade", {
      id: "in_upgrade",
      status: "open",
      amount_due: 1000,
      amount_remaining: 1000,
    } as Stripe.Invoice);
    expect(
      (
        await deliver(
          destination,
          "evt_upgrade",
          "customer.subscription.updated",
          { id: sub.id, customer },
          destination,
          undefined,
          true,
        )
      ).status,
    ).toBe(200);
    expect(
      await db.user.findUniqueOrThrow({ where: { id: "user_test" } }),
    ).toMatchObject({
      plan: "pro",
      pending_plan: "scale",
      stripe_account_id: destination,
    });
    invoices.get(destination + ":in_upgrade")!.status = "paid";
    invoices.get(destination + ":in_upgrade")!.amount_remaining = 0;
    expect(
      (
        await deliver(
          destination,
          "evt_recovery",
          "invoice.paid",
          paid,
          destination,
          undefined,
          true,
        )
      ).status,
    ).toBe(200);
    expect(
      await db.user.findUniqueOrThrow({ where: { id: "user_test" } }),
    ).toMatchObject({ plan: "scale", pending_plan: null });
    expect(dispatched).not.toHaveBeenCalled();
  });
  test("paid invoice activates the destination; delayed source cancellation cannot remove access", async () => {
    await seed();
    const result = await deliver(destination, "evt_paid", "invoice.paid", {
      id: "in_paid",
      customer,
      parent: { subscription_details: { subscription: "sub_destination" } },
    });
    expect(result.status).toBe(200);
    const user = await db.user.findUniqueOrThrow({
      where: { id: "user_test" },
    });
    expect(user.stripe_account_id).toBe(destination);
    expect(user.stripe_subscription_id).toBe("sub_destination");
    expect(user.plan).toBe("pro");
    expect(user.legacy_hd).toBe(true);
    objects.get(source + ":sub_source")!.status = "canceled";
    expect(
      (
        await deliver(
          source,
          "evt_old_delete",
          "customer.subscription.deleted",
          { id: "sub_source", customer },
        )
      ).status,
    ).toBe(200);
    expect(
      (await db.user.findUniqueOrThrow({ where: { id: user.id } })).plan,
    ).toBe("pro");
    expect(dispatched).not.toHaveBeenCalled();
  });
  test("wrong secret is rejected before an inbox row or account mutation", async () => {
    await seed();
    expect(
      (
        await deliver(
          destination,
          "evt_wrong",
          "invoice.paid",
          { id: "in_paid", customer },
          source,
        )
      ).status,
    ).toBe(400);
    expect(await db.stripeWebhookEvent.count()).toBe(0);
  });
  test("a source that can still renew blocks destination activation", async () => {
    await seed();
    objects.get(source + ":sub_source")!.cancel_at_period_end = false;
    expect(
      (
        await deliver(destination, "evt_unsafe", "invoice.paid", {
          id: "in_paid",
          customer,
          parent: { subscription_details: { subscription: "sub_destination" } },
        })
      ).status,
    ).toBe(503);
    expect(
      (await db.user.findUniqueOrThrow({ where: { id: "user_test" } }))
        .stripe_account_id,
    ).toBeNull();
  });
  test("failed first payment retains prepaid data and routes only payment recovery to the destination", async () => {
    await seed();
    objects.get(destination + ":sub_destination")!.status = "past_due";
    const result = await deliver(
      destination,
      "evt_fail",
      "invoice.payment_failed",
      {
        id: "in_failed",
        customer,
        parent: { subscription_details: { subscription: "sub_destination" } },
      },
    );
    expect(result.status).toBe(200);
    const record = await db.stripeAccountMigration.findUniqueOrThrow({
      where: { id: "migration_test" },
    });
    expect(record.recovery_required).toBe(true);
    expect(record.stage).toBe("destination_staged");
    expect(
      await withUserBillingAccount(
        "user_test",
        "portal",
        async () => stripeAccountContext(),
        { db, client: async (id) => client(id) },
      ),
    ).toMatchObject({
      accountId: destination,
      customerId: customer,
      recoveryOnly: true,
    });
    await expect(
      withUserBillingAccount("user_test", "checkout", async () => true, {
        db,
        client: async (id) => client(id),
      }),
    ).rejects.toThrow();
  });
  test("real cancellation survives a partial API failure and prevents a late paid event activating the import", async () => {
    await seed();
    const sub = objects.get(source + ":sub_source")!;
    failCancel = true;
    const payload = { id: sub.id, customer };
    expect(
      (
        await deliver(
          source,
          "evt_cancel",
          "customer.subscription.updated",
          payload,
          source,
          { cancel_at_period_end: false },
        )
      ).status,
    ).toBe(503);
    expect(
      (
        await db.stripeAccountMigration.findUniqueOrThrow({
          where: { id: "migration_test" },
        })
      ).cancellation_requested,
    ).toBe(true);
    failCancel = false;
    expect(
      (
        await deliver(
          source,
          "evt_cancel",
          "customer.subscription.updated",
          payload,
          source,
          { cancel_at_period_end: false },
        )
      ).status,
    ).toBe(200);
    expect(
      (
        await db.stripeAccountMigration.findUniqueOrThrow({
          where: { id: "migration_test" },
        })
      ).stage,
    ).toBe("cancelled");
    expect(schedules.get(destination + ":sub_sched_import")!.status).toBe(
      "canceled",
    );
  });
  test("concurrent duplicates run one transaction and account/mode event keys do not collide", async () => {
    const inbox = createPrismaWebhookInbox(db);
    const results = await Promise.all(
      Array.from({ length: 5 }, () =>
        inbox.claim(source, false, "evt_same", "customer.updated"),
      ),
    );
    expect(results.filter((r) => r.kind === "claimed")).toHaveLength(1);
    expect(
      (await inbox.claim(destination, false, "evt_same", "customer.updated"))
        .kind,
    ).toBe("claimed");
    expect(
      (await inbox.claim(source, true, "evt_same", "customer.updated")).kind,
    ).toBe("claimed");
  });
  test("stale proof and same-customer wrong-account writers cannot overwrite a new owner", async () => {
    await seed();
    const store = createPrismaMigrationStore(db);
    const stale = (await store.byCustomer(source, customer))!;
    await store.requestCancellation(stale.operationId, stale.appUserId);
    await expect(
      store.commit(stale, { kind: "review", reason: "stale" }),
    ).rejects.toThrow("changed");
    await db.user.update({
      where: { id: "user_test" },
      data: {
        stripe_account_id: destination,
        stripe_subscription_id: "sub_destination",
      },
    });
    const result = await runWithStripeAccount(
      { accountId: source, client: client(source) },
      () =>
        guardedBillingUpdate(
          customer,
          "sub_source",
          { stripe_customer_id: customer },
          { plan: "free" },
        ),
    );
    expect(result.count).toBe(0);
    expect(
      (await db.user.findUniqueOrThrow({ where: { id: "user_test" } })).plan,
    ).toBe("pro");
  });
  test.each(["apple", "affiliate"])(
    "activation preserves the %s entitlement owner",
    async (provider) => {
      await seed(provider);
      expect(
        (
          await deliver(destination, "evt_alternate", "invoice.paid", {
            id: "in_paid",
            customer,
            parent: {
              subscription_details: { subscription: "sub_destination" },
            },
          })
        ).status,
      ).toBe(200);
      const user = await db.user.findUniqueOrThrow({
        where: { id: "user_test" },
      });
      expect(user.subscription_provider).toBe(provider);
      expect(user.plan).toBe("pro");
      expect(
        migrationRecord(
          await db.stripeAccountMigration.findUniqueOrThrow({
            where: { id: "migration_test" },
          }),
        ).stage,
      ).toBe("destination_active");
    },
  );
});

describe("ordinary new-US customer risk dispatch through the real database", () => {
  async function seedNewCustomer(provider = "stripe") {
    await db.user.create({ data: { id: "new_us", name: "Synthetic", email: "new-us@example.invalid", stripe_account_id: destination, stripe_customer_id: customer, stripe_subscription_id: "sub_new_us", subscription_provider: provider, subscription_status: "active", plan: "pro" } });
    const sub = subscription(destination, "sub_new_us", "price_destination", paidThrough);
    invoices.set(destination + ":in_source_original", { id: "in_source_original", customer, parent: { subscription_details: { subscription: "sub_new_us" } } } as Stripe.Invoice);
    vi.spyOn(client(destination).subscriptions, "cancel").mockImplementation(async id => { const value = objects.get(destination+":"+id)!; value.status = "canceled"; return structuredClone(value) as Stripe.Response<Stripe.Subscription>; });
    return sub;
  }
  const riskEvent = (type: string) => ({ id: "evt_risk", type, data: { object: type === "charge.refunded" ? { id: "ch_owned", customer, refunded: true } : { id: "dp_owned", charge: "ch_owned" } } }) as Stripe.Event;
  test.each(["charge.refunded", "charge.dispute.created", "radar.early_fraud_warning.created"])("%s cancels only the owned invoice subscription", async type => {
    await seedNewCustomer();
    await runWithStripeAccount({ accountId: destination, client: client(destination) }, () => dispatchBillingEvent(riskEvent(type)));
    expect(await db.user.findUniqueOrThrow({ where: { id: "new_us" } })).toMatchObject({ plan: "free", subscription_status: "canceled", stripe_account_id: destination });
  });
  test("a historical subscription's refund cannot cancel the current subscription", async () => {
    const sub = await seedNewCustomer();
    invoices.set(destination+":in_source_original", { customer, parent: { subscription_details: { subscription: "sub_old" } } } as Stripe.Invoice);
    await runWithStripeAccount({ accountId: destination, client: client(destination) }, () => dispatchBillingEvent(riskEvent("charge.refunded")));
    expect(sub.status).toBe("active");
    expect((await db.user.findUniqueOrThrow({ where: { id: "new_us" } })).plan).toBe("pro");
  });
  test.each(["apple", "affiliate"])("risk does not cancel or replace an independent %s grant", async provider => {
    const sub = await seedNewCustomer(provider);
    await runWithStripeAccount({ accountId: destination, client: client(destination) }, () => dispatchBillingEvent(riskEvent("charge.dispute.created")));
    expect(sub.status).toBe("active");
    expect((await db.user.findUniqueOrThrow({ where: { id: "new_us" } })).subscription_provider).toBe(provider);
  });
  test("the same customer ID in the wrong account has no authority", async () => {
    const sub = await seedNewCustomer();
    invoices.set(source+":in_source_original", { customer, parent: { subscription_details: { subscription: "sub_new_us" } } } as Stripe.Invoice);
    await runWithStripeAccount({ accountId: source, client: client(source) }, () => dispatchBillingEvent(riskEvent("charge.dispute.created")));
    expect(sub.status).toBe("active");
  });
});

test("payment recovery rejects an invoice starting outside the original renewal boundary", async () => {
  await seed();
  objects.get(destination+":sub_destination")!.items.data[0].current_period_start += 60;
  const response = await deliver(destination, "evt_wrong_recovery_period", "invoice.payment_action_required", { id: "in_wrong_period", customer, parent: { subscription_details: { subscription: "sub_destination" } } });
  expect(response.status).toBe(503);
  expect((await createPrismaMigrationStore(db).byCustomer(source, customer))?.recoveryRequired).toBe(false);
});
test("payment recovery rechecks that source billing cannot still renew", async () => {
  await seed();
  objects.get(source+":sub_source")!.cancel_at_period_end = false;
  const response = await deliver(destination, "evt_source_resumed_recovery", "invoice.payment_action_required", { id: "in_wrong_source", customer, parent: { subscription_details: { subscription: "sub_destination" } } });
  expect(response.status).toBe(503);
  expect((await createPrismaMigrationStore(db).byCustomer(source, customer))?.recoveryRequired).toBe(false);
});
