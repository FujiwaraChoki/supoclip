// @vitest-environment node
import { readFileSync } from "node:fs";

import { PGlite } from "@electric-sql/pglite";
import { uuid_ossp } from "@electric-sql/pglite/contrib/uuid_ossp";
import Stripe from "stripe";

import {
  adjustForDispute,
  earningsTotals,
  recordInvoiceCommission,
  reverseRefundedCommission,
  type CommissionStore,
  type SqlRunner,
} from "./affiliate-commissions";
import { claimPayout, creatorBalances, runCreatorPayouts } from "./affiliate-payouts";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

type Queryable = Pick<PGlite, "query">;

function runner(db: Queryable): SqlRunner {
  return {
    query: async <T,>(sql: string, params: unknown[] = []) => (await db.query<T>(sql, params)).rows,
    execute: async (sql, params = []) => (await db.query(sql, params)).affectedRows ?? 0,
  };
}

function storeFor(db: PGlite): CommissionStore {
  return { ...runner(db), transaction: (callback) => db.transaction((tx) => callback(runner(tx))) };
}

const DAY = 24 * 60 * 60;
const T0 = Date.UTC(2026, 10, 1) / 1000; // 1 Nov 2026

/** Stripe as the ledger sees it: invoice payments, their charges and balance transactions. */
function fakeStripe(charges: Record<string, { invoice: string; settled: number; currency?: string }>) {
  const transfers: Stripe.Transfer[] = [];
  const client = {
    invoicePayments: {
      list: vi.fn(async ({ invoice }: { invoice: string }) => ({
        data: Object.entries(charges)
          .filter(([, charge]) => charge.invoice === invoice)
          .map(([id]) => ({ status: "paid", payment: { type: "payment_intent", payment_intent: `pi_${id}` } })),
      })),
    },
    paymentIntents: {
      retrieve: vi.fn(async (id: string) => {
        const chargeId = id.replace(/^pi_/, "");
        const charge = charges[chargeId];
        return {
          id,
          latest_charge: {
            id: chargeId,
            status: "succeeded",
            amount: charge.settled,
            currency: charge.currency ?? "usd",
            balance_transaction: { amount: charge.settled, currency: charge.currency ?? "usd" },
          },
        };
      }),
    },
    invoices: { retrieve: vi.fn() },
    accounts: {
      retrieve: vi.fn(async (id: string) => ({ id, payouts_enabled: true, capabilities: { transfers: "active" } })),
    },
    transfers: {
      list: vi.fn(async ({ transfer_group }: { transfer_group: string }) => ({
        data: transfers.filter((transfer) => transfer.transfer_group === transfer_group),
      })),
      create: vi.fn(async (params: Stripe.TransferCreateParams) => {
        const transfer = { id: `tr_${transfers.length + 1}`, ...params } as unknown as Stripe.Transfer;
        transfers.push(transfer);
        return transfer;
      }),
    },
  };
  return { client: client as unknown as Stripe, mock: client, transfers };
}

function invoice(overrides: Partial<Stripe.Invoice> & { id: string; code?: string; buyer?: string; paidAt?: number }) {
  const { code, buyer = "buyer", paidAt = T0, ...rest } = overrides;
  return {
    object: "invoice",
    status: "paid",
    amount_paid: 999,
    total_taxes: [],
    customer: "cus_buyer",
    discounts: [],
    status_transitions: { paid_at: paidAt },
    parent: {
      subscription_details: {
        subscription: "sub_1",
        metadata: { ...(code ? { creator_code: code } : {}), userId: buyer },
      },
    },
    ...rest,
  } as unknown as Stripe.Invoice;
}

describe("creator commission ledger (real Postgres)", () => {
  let db: PGlite;
  let store: CommissionStore;

  beforeAll(async () => {
    db = new PGlite({ extensions: { uuid_ossp } });
    await db.exec(read("../../../init.sql"));
    // Production gets these tables from the migration, so test that DDL on the previous schema.
    await db.exec(`
      DROP TABLE affiliate_commissions; DROP TABLE affiliate_payouts; DROP TABLE affiliate_referrals;
      ALTER TABLE affiliates DROP COLUMN stripe_connect_account_id, DROP COLUMN stripe_connect_platform_id,
        DROP COLUMN payout_country, DROP COLUMN payouts_enabled, DROP COLUMN country;
    `);
    await db.exec(read("../../prisma/migrations/20261124120000_creator_commissions/migration.sql"));
    store = storeFor(db);
  });

  afterAll(async () => {
    await db.close();
  });

  beforeEach(async () => {
    await db.exec(`
      DELETE FROM affiliate_commissions; DELETE FROM affiliate_payouts; DELETE FROM affiliate_referrals;
      DELETE FROM affiliates; DELETE FROM users;
      INSERT INTO users (id, name, email, stripe_customer_id) VALUES
        ('maya', 'Maya', 'maya@example.com', NULL),
        ('buyer', 'Buyer', 'buyer@example.com', 'cus_buyer');
      INSERT INTO affiliates (id, user_id, slug, status, platform, profile_url, audience_size, terms_accepted_at,
                              stripe_promotion_code_id, stripe_connect_account_id, stripe_connect_platform_id, payouts_enabled,
                              country)
      VALUES ('aff-maya', 'maya', 'maya', 'approved', 'youtube', 'https://youtube.com/@maya', '10k-100k', now(),
              'promo_maya', 'acct_maya', 'default', true, 'US');
    `);
  });

  const context = (client: Stripe, now = new Date(T0 * 1000)) => ({ store, client, accountId: "acct_source", now });
  const rows = async () =>
    (await db.query<{ kind: string; amount: number; base_amount: number; currency: string; available_at: Date }>(
      "SELECT kind, amount, base_amount, currency, available_at FROM affiliate_commissions ORDER BY occurred_at, created_at",
    )).rows;

  it("pays 30% of what the customer paid after the creator discount, held for 30 days", async () => {
    // Pro at $9.99 with 20% off: the customer pays $7.99.
    const { client } = fakeStripe({ ch_1: { invoice: "in_1", settled: 799 } });
    await recordInvoiceCommission(invoice({ id: "in_1", amount_paid: 799, code: "MAYA" }), context(client));

    const [row] = await rows();
    expect(row).toMatchObject({ kind: "earned", base_amount: 799, amount: 240, currency: "usd" });
    expect(new Date(row.available_at).toISOString()).toBe("2026-12-01T00:00:00.000Z");
  });

  it("leaves tax out of the base", async () => {
    // $9.99 plus $2.00 tax collected on top.
    const { client } = fakeStripe({ ch_1: { invoice: "in_1", settled: 1199 } });
    await recordInvoiceCommission(
      invoice({ id: "in_1", amount_paid: 1199, total_taxes: [{ amount: 200 } as Stripe.Invoice.TotalTax], code: "maya" }),
      context(client),
    );
    expect((await rows())[0]).toMatchObject({ base_amount: 999, amount: 300 });
  });

  it("pays on what reached our balance when the customer paid in another currency", async () => {
    // A Brazilian customer pays in BRL; the charge settles as $9.40 in the USD balance.
    const { client } = fakeStripe({ ch_1: { invoice: "in_1", settled: 940, currency: "usd" } });
    await recordInvoiceCommission(invoice({ id: "in_1", amount_paid: 4990, currency: "brl", code: "maya" }), context(client));
    expect((await rows())[0]).toMatchObject({ base_amount: 940, amount: 282, currency: "usd" });
  });

  it("counts a USD price paid into a balance in another currency in USD", async () => {
    // A subscription still billed on the Swiss account, whose balance settles in CHF.
    const { client, mock } = fakeStripe({ ch_1: { invoice: "in_1", settled: 880, currency: "chf" } });
    mock.paymentIntents.retrieve.mockResolvedValue({
      id: "pi_ch_1",
      latest_charge: { id: "ch_1", status: "succeeded", amount: 999, currency: "usd", balance_transaction: { amount: 880, currency: "chf" } },
    });
    await recordInvoiceCommission(invoice({ id: "in_1", code: "maya" }), context(client));
    expect((await rows())[0]).toMatchObject({ base_amount: 999, amount: 300, currency: "usd" });
  });

  it("keeps one 12-month window per customer when their subscription moves accounts or restarts", async () => {
    const first = Date.UTC(2026, 0, 15) / 1000;
    const { client } = fakeStripe({ ch_1: { invoice: "in_1", settled: 999 } });
    await recordInvoiceCommission(invoice({ id: "in_1", code: "maya", paidAt: first }), context(client));

    // Eleven months later the same customer pays on a new subscription (moved to the US account).
    const moved = (id: string, paidAt: number) =>
      invoice({
        id,
        code: "maya",
        paidAt,
        parent: { subscription_details: { subscription: "sub_us", metadata: { creator_code: "maya", userId: "buyer" } } },
      } as never);
    const { client: us } = fakeStripe({ ch_2: { invoice: "in_2", settled: 999 }, ch_3: { invoice: "in_3", settled: 999 } });
    await recordInvoiceCommission(moved("in_2", Date.UTC(2026, 11, 15) / 1000), context(us));
    await recordInvoiceCommission(moved("in_3", Date.UTC(2027, 0, 16) / 1000), context(us));
    expect(await rows()).toHaveLength(2);
  });

  it("counts a payment once although Stripe sends invoice.paid and invoice.payment_succeeded, twice each", async () => {
    const { client } = fakeStripe({ ch_1: { invoice: "in_1", settled: 999 } });
    const paid = invoice({ id: "in_1", code: "maya" });
    for (let i = 0; i < 4; i++) await recordInvoiceCommission(paid, context(client));
    expect(await rows()).toHaveLength(1);
  });

  it("finds the creator of a subscription bought before checkout stored the code, through its discount", async () => {
    const { client, mock } = fakeStripe({ ch_1: { invoice: "in_1", settled: 799 } });
    mock.invoices.retrieve.mockResolvedValue({ discounts: [{ promotion_code: "promo_maya" }] });
    await recordInvoiceCommission(invoice({ id: "in_1", amount_paid: 799, discounts: ["di_1"] }), context(client));
    expect(await rows()).toHaveLength(1);

    // Months later the discount has ended, but the referral is remembered.
    const { client: later } = fakeStripe({ ch_2: { invoice: "in_2", settled: 999 } });
    await recordInvoiceCommission(invoice({ id: "in_2", paidAt: T0 + 95 * DAY }), context(later));
    expect((await rows()).map((row) => row.amount)).toEqual([240, 300]);
  });

  it("pays nothing for subscriptions without a creator, house codes or a creator's own purchase", async () => {
    const { client, mock } = fakeStripe({ ch_1: { invoice: "in_1", settled: 999 } });
    mock.invoices.retrieve.mockResolvedValue({ discounts: [{ promotion_code: "promo_producthunt" }] });
    await recordInvoiceCommission(invoice({ id: "in_1" }), context(client));
    await recordInvoiceCommission(invoice({ id: "in_1", discounts: ["di_house"] }), context(client));
    await recordInvoiceCommission(invoice({ id: "in_1", code: "maya", buyer: "maya" }), context(client));
    expect(await rows()).toHaveLength(0);
  });

  it("earns for 12 months from the first payment, then stops", async () => {
    const months = [0, 1, 11, 12, 13];
    for (const month of months) {
      const paidAt = Date.UTC(2026, 10 + month, 1) / 1000;
      const { client } = fakeStripe({ [`ch_${month}`]: { invoice: `in_${month}`, settled: 999 } });
      await recordInvoiceCommission(invoice({ id: `in_${month}`, code: "maya", paidAt }), context(client));
    }
    expect(await rows()).toHaveLength(3);
  });

  it("counts a yearly plan once", async () => {
    const { client } = fakeStripe({ ch_y1: { invoice: "in_y1", settled: 9990 } });
    await recordInvoiceCommission(invoice({ id: "in_y1", amount_paid: 9990, code: "maya" }), context(client));
    const renewal = Date.UTC(2027, 10, 1) / 1000;
    const { client: next } = fakeStripe({ ch_y2: { invoice: "in_y2", settled: 9990 } });
    await recordInvoiceCommission(invoice({ id: "in_y2", amount_paid: 9990, paidAt: renewal }), context(next));
    expect((await rows()).map((row) => row.amount)).toEqual([2997]);
  });

  it("stops earning while the creator is out of the program", async () => {
    await db.exec("UPDATE affiliates SET status = 'revoked'");
    const { client } = fakeStripe({ ch_1: { invoice: "in_1", settled: 999 } });
    await recordInvoiceCommission(invoice({ id: "in_1", code: "maya" }), context(client));
    expect(await rows()).toHaveLength(0);
  });

  it.each([["elsewhere", "'ZZ'"], ["not given yet", "NULL"]])(
    "earns nothing for a creator whose country is %s",
    async (_label, country) => {
      await db.exec(`UPDATE affiliates SET country = ${country}`);
      const { client } = fakeStripe({ ch_1: { invoice: "in_1", settled: 999 } });
      await recordInvoiceCommission(invoice({ id: "in_1", code: "maya" }), context(client));
      expect(await rows()).toHaveLength(0);
    },
  );

  it("ignores unpaid, free and non-subscription invoices", async () => {
    const { client } = fakeStripe({ ch_1: { invoice: "in_1", settled: 999 } });
    await recordInvoiceCommission(invoice({ id: "in_1", code: "maya", status: "open" }), context(client));
    await recordInvoiceCommission(invoice({ id: "in_1", code: "maya", amount_paid: 0 }), context(client));
    await recordInvoiceCommission(invoice({ id: "in_1", code: "maya", parent: null }), context(client));
    expect(await rows()).toHaveLength(0);
  });

  describe("refunds and disputes", () => {
    const charge = (amountRefunded: number) =>
      ({ id: "ch_1", amount: 999, amount_refunded: amountRefunded, refunded: amountRefunded === 999 }) as Stripe.Charge;

    beforeEach(async () => {
      const { client } = fakeStripe({ ch_1: { invoice: "in_1", settled: 999 } });
      await recordInvoiceCommission(invoice({ id: "in_1", code: "maya" }), context(client));
    });

    it("takes back the refunded share, adding up partial refunds, once per refund", async () => {
      await reverseRefundedCommission(charge(500), store);
      await reverseRefundedCommission(charge(500), store);
      await reverseRefundedCommission(charge(999), store);
      expect((await rows()).map((row) => [row.kind, row.amount])).toEqual([
        ["earned", 300],
        ["refund", -150],
        ["refund", -150],
      ]);
    });

    it("takes back what is left when the customer disputes, and returns it when we win", async () => {
      await reverseRefundedCommission(charge(500), store);
      const dispute = { id: "dp_1", charge: "ch_1", status: "needs_response" } as Stripe.Dispute;
      await adjustForDispute(dispute, false, store);
      await adjustForDispute(dispute, false, store);
      await adjustForDispute({ ...dispute, status: "won" } as Stripe.Dispute, true, store);
      expect((await rows()).map((row) => [row.kind, row.amount])).toEqual([
        ["earned", 300],
        ["refund", -150],
        ["dispute", -150],
        ["dispute_won", 150],
      ]);
    });

    it("ignores inquiries and lost disputes after the reversal", async () => {
      await adjustForDispute({ id: "dp_1", charge: "ch_1", status: "warning_needs_response" } as Stripe.Dispute, false, store);
      await adjustForDispute({ id: "dp_1", charge: "ch_1", status: "lost" } as Stripe.Dispute, true, store);
      expect(await rows()).toHaveLength(1);
    });
  });

  describe("payouts", () => {
    const afterHold = new Date((T0 + 31 * DAY) * 1000);

    async function earn(id: string, settled: number) {
      const { client } = fakeStripe({ [`ch_${id}`]: { invoice: `in_${id}`, settled } });
      const sub = `sub_${id}`;
      await recordInvoiceCommission(
        invoice({
          id: `in_${id}`,
          code: "maya",
          parent: { subscription_details: { subscription: sub, metadata: { creator_code: "maya", userId: "buyer" } } },
        } as never),
        context(client),
      );
    }

    it("waits until the hold is over and the balance reaches $25", async () => {
      await earn("a", 999);
      expect(await claimPayout(store, { userId: "maya", currency: "usd", connectAccountId: "acct_maya", adminId: null, now: afterHold })).toBeNull();
      expect((await db.query("SELECT * FROM affiliate_payouts")).rows).toHaveLength(0);
    });

    it("pays every available commission in one transfer, once", async () => {
      for (const id of ["a", "b", "c", "d", "e", "f", "g", "h", "i"]) await earn(id, 999); // 9 × $3.00
      const { client, transfers } = fakeStripe({});

      const results = await runCreatorPayouts(store, client, { adminId: "maya", now: afterHold });
      expect(results).toEqual([expect.objectContaining({ outcome: "paid", amount: 2700, currency: "usd" })]);
      expect(transfers).toHaveLength(1);
      expect(transfers[0]).toMatchObject({ amount: 2700, currency: "usd", destination: "acct_maya" });

      await runCreatorPayouts(store, client, { adminId: "maya", now: afterHold });
      expect(transfers).toHaveLength(1);
      expect(await earningsTotals(store, "maya", afterHold)).toEqual([
        { currency: "usd", pending: 0, available: 0, in_transit: 0, paid: 2700 },
      ]);
    });

    it("deducts a refund of already-paid commission from the next payout", async () => {
      for (const id of ["a", "b", "c", "d", "e", "f", "g", "h", "i"]) await earn(id, 999);
      const { client } = fakeStripe({});
      await runCreatorPayouts(store, client, { adminId: null, now: afterHold });
      await reverseRefundedCommission({ id: "ch_a", amount: 999, amount_refunded: 999 } as Stripe.Charge, store, afterHold);

      const [balance] = await creatorBalances(store, afterHold);
      expect(balance).toMatchObject({ available: -300 });
    });

    it("gives the money back to the balance when Stripe refuses the transfer", async () => {
      for (const id of ["a", "b", "c", "d", "e", "f", "g", "h", "i"]) await earn(id, 999);
      const { client, mock } = fakeStripe({});
      // generate() gives no HTTP status; Stripe sends 400 for this.
      mock.transfers.create.mockImplementation(async () => {
        const error = Stripe.errors.StripeError.generate({ type: "invalid_request_error", message: "Insufficient funds" });
        Object.assign(error, { statusCode: 400 });
        throw error;
      });

      const [result] = await runCreatorPayouts(store, client, { adminId: null, now: afterHold });
      expect(result).toMatchObject({ outcome: "failed", reason: "Insufficient funds" });
      expect(await earningsTotals(store, "maya", afterHold)).toEqual([
        { currency: "usd", pending: 0, available: 2700, in_transit: 0, paid: 0 },
      ]);
    });

    it("finishes a payout whose outcome was unknown without paying twice", async () => {
      for (const id of ["a", "b", "c", "d", "e", "f", "g", "h", "i"]) await earn(id, 999);
      const { client, mock, transfers } = fakeStripe({});
      const create = mock.transfers.create.getMockImplementation()!;
      // The transfer goes through but the response is lost.
      mock.transfers.create.mockImplementationOnce(async (params) => {
        await create(params);
        throw new Error("socket hang up");
      });

      const [first] = await runCreatorPayouts(store, client, { adminId: null, now: afterHold });
      expect(first.outcome).toBe("pending");
      expect((await earningsTotals(store, "maya", afterHold))[0]).toMatchObject({ available: 0, in_transit: 2700 });

      const [second] = await runCreatorPayouts(store, client, { adminId: null, now: afterHold });
      expect(second.outcome).toBe("paid");
      expect(transfers).toHaveLength(1);
    });

    it("skips creators who haven't finished Stripe onboarding", async () => {
      for (const id of ["a", "b", "c", "d", "e", "f", "g", "h", "i"]) await earn(id, 999);
      const { client, mock, transfers } = fakeStripe({});
      mock.accounts.retrieve.mockResolvedValue({ id: "acct_maya", payouts_enabled: false, capabilities: { transfers: "inactive" } });

      const [result] = await runCreatorPayouts(store, client, { adminId: null, now: afterHold });
      expect(result.outcome).toBe("skipped");
      expect(transfers).toHaveLength(0);
      expect((await db.query<{ payouts_enabled: boolean }>("SELECT payouts_enabled FROM affiliates")).rows[0].payouts_enabled).toBe(false);
    });
  });
});
