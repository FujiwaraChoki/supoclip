import { randomUUID } from "node:crypto";
import Stripe from "stripe";

import {
  COMMISSION_HOLD_DAYS,
  PAYOUT_CURRENCY,
  COMMISSION_RATE_BPS,
  commissionAmount,
  commissionBase,
  isPayoutCountry,
  withinCommissionWindow,
} from "@/lib/creator-commission";
import { stripeAccountContext, stripeAccountRole, stripeAccountsEnabled } from "@/lib/stripe-accounts";
import { invoiceSubscriptionId, stripeObjectId } from "@/lib/stripe-migration-engine";
import { getPrismaClient } from "@/server/prisma";
import { getServerStripeClient } from "@/server/stripe";

/**
 * Creator commission ledger.
 *
 * A subscription bought with a creator's code is a referral. Each paid invoice of that
 * subscription in its first COMMISSION_MONTHS earns the creator COMMISSION_PERCENT of what
 * actually reached our Stripe balance for it, without tax: one `earned` row per charge,
 * payable COMMISSION_HOLD_DAYS later. Refunds and disputes add negative rows straight away,
 * so they come off the creator's next payout even when the commission was already paid.
 *
 * Every row has an idempotency key derived from Stripe object IDs, so the webhook can be
 * delivered any number of times (and invoice.paid and invoice.payment_succeeded both
 * arrive for one payment) without counting anything twice.
 */

/** Raw SQL against the app database. Production uses Prisma; tests use PGlite. */
export type SqlRunner = {
  query<T = Record<string, unknown>>(sql: string, params?: unknown[]): Promise<T[]>;
  execute(sql: string, params?: unknown[]): Promise<number>;
};
export type CommissionStore = SqlRunner & {
  transaction<T>(callback: (tx: SqlRunner) => Promise<T>): Promise<T>;
};

type PrismaLike = {
  $queryRawUnsafe<T = unknown>(query: string, ...values: unknown[]): Promise<T>;
  $executeRawUnsafe(query: string, ...values: unknown[]): Promise<number>;
};

function runner(client: PrismaLike): SqlRunner {
  return {
    query: <T,>(sql: string, params: unknown[] = []) => client.$queryRawUnsafe<T[]>(sql, ...params),
    execute: (sql, params = []) => client.$executeRawUnsafe(sql, ...params),
  };
}

export function prismaCommissionStore(prisma = getPrismaClient()): CommissionStore {
  return {
    ...runner(prisma),
    transaction: (callback) => prisma.$transaction((tx) => callback(runner(tx))),
  };
}

export type Referral = {
  id: string;
  affiliate_user_id: string;
  customer_user_id: string | null;
  stripe_subscription_id: string;
  code: string;
  first_paid_at: Date;
};

type Attribution = { affiliateUserId: string; code: string };

type CommissionRow = {
  id: string;
  affiliate_user_id: string;
  referral_id: string;
  stripe_account_id: string;
  stripe_invoice_id: string | null;
  currency: string;
  base_amount: number;
  amount: number;
};

export type LedgerContext = {
  store: CommissionStore;
  client: Stripe;
  /** The Stripe account the event came from. */
  accountId: string;
  now?: Date;
};

const DAY_MS = 24 * 60 * 60 * 1000;

function unixDate(seconds: number | null | undefined, fallback: Date): Date {
  return seconds ? new Date(seconds * 1000) : fallback;
}

function invoiceTax(invoice: Stripe.Invoice): number {
  const legacy = invoice as Stripe.Invoice & { tax?: number | null };
  if (invoice.total_taxes) return invoice.total_taxes.reduce((sum, tax) => sum + tax.amount, 0);
  return legacy.tax ?? 0;
}

/** The account events are processed for: the routed account, or the single legacy account. */
export function currentStripeAccountId(): string {
  return stripeAccountContext()?.accountId || process.env.STRIPE_SOURCE_ACCOUNT_ID || "default";
}

/** Source promotion code IDs behind the given IDs (the destination account uses mapped copies). */
function sourcePromotionCodeIds(ids: string[], accountId: string): string[] {
  if (!stripeAccountsEnabled() || stripeAccountRole(accountId) !== "destination") return ids;
  let map: Record<string, unknown> = {};
  try {
    map = JSON.parse(process.env.STRIPE_DESTINATION_PROMOTION_CODE_MAP || "{}");
  } catch {
    return [];
  }
  return Object.entries(map)
    .filter(([, destination]) => typeof destination === "string" && ids.includes(destination))
    .map(([source]) => source);
}

/** Which approved creator, if any, a subscription's invoice was bought through. */
export async function resolveAttribution(
  invoice: Stripe.Invoice,
  { store, client, accountId }: LedgerContext,
): Promise<Attribution | null> {
  const code = invoice.parent?.subscription_details?.metadata?.creator_code;
  if (code) {
    const rows = await store.query<{ user_id: string; slug: string }>(
      "SELECT user_id, slug FROM affiliates WHERE slug = $1 AND status = 'approved'",
      [code.toLowerCase()],
    );
    if (rows[0]) return { affiliateUserId: rows[0].user_id, code: rows[0].slug };
  }

  // Subscriptions from before checkout stored the code: the creator discount says who it was.
  let discounts = invoice.discounts ?? [];
  if (discounts.some((discount) => typeof discount === "string")) {
    discounts = (await client.invoices.retrieve(invoice.id!, { expand: ["discounts"] })).discounts ?? [];
  }
  const promotionCodes = discounts
    .map((discount) => (typeof discount === "string" ? undefined : stripeObjectId(discount.promotion_code)))
    .filter((id): id is string => Boolean(id));
  const sourceIds = sourcePromotionCodeIds(promotionCodes, accountId);
  if (sourceIds.length === 0) return null;
  const rows = await store.query<{ user_id: string; slug: string }>(
    "SELECT user_id, slug FROM affiliates WHERE stripe_promotion_code_id = ANY($1::text[]) AND status = 'approved' AND slug IS NOT NULL",
    [sourceIds],
  );
  return rows[0] ? { affiliateUserId: rows[0].user_id, code: rows[0].slug } : null;
}

async function findReferral(store: SqlRunner, subscriptionId: string): Promise<Referral | null> {
  const rows = await store.query<Referral>(
    `SELECT id, affiliate_user_id, customer_user_id, stripe_subscription_id, code, first_paid_at
       FROM affiliate_referrals WHERE stripe_subscription_id = $1`,
    [subscriptionId],
  );
  return rows[0] ?? null;
}

async function customerUserId(invoice: Stripe.Invoice, store: SqlRunner): Promise<string | null> {
  const fromMetadata = invoice.parent?.subscription_details?.metadata?.userId;
  if (fromMetadata) return fromMetadata;
  const customerId = stripeObjectId(invoice.customer);
  if (!customerId) return null;
  const rows = await store.query<{ id: string }>(
    "SELECT id FROM users WHERE stripe_customer_id = $1 LIMIT 2",
    [customerId],
  );
  return rows.length === 1 ? rows[0].id : null;
}

type SettledCharge = {
  chargeId: string;
  /** What reached our balance, before Stripe fees, in the settlement currency. */
  amount: number;
  currency: string;
  /** The charge itself, in the invoice's currency. */
  chargeAmount: number;
  chargeCurrency: string;
};

/** Charges that paid the invoice and what each put in our balance (before Stripe fees). */
export async function settledCharges(invoice: Stripe.Invoice, client: Stripe): Promise<SettledCharge[]> {
  const payments = await client.invoicePayments.list({ invoice: invoice.id!, limit: 20 });
  const settled: SettledCharge[] = [];
  for (const payment of payments.data) {
    if (payment.status !== "paid") continue;
    let charge: Stripe.Charge | null = null;
    if (payment.payment.type === "payment_intent" && payment.payment.payment_intent) {
      const intent = await client.paymentIntents.retrieve(stripeObjectId(payment.payment.payment_intent)!, {
        expand: ["latest_charge.balance_transaction"],
      });
      charge = typeof intent.latest_charge === "object" ? intent.latest_charge : null;
    } else if (payment.payment.type === "charge" && payment.payment.charge) {
      charge = await client.charges.retrieve(stripeObjectId(payment.payment.charge)!, {
        expand: ["balance_transaction"],
      });
    }
    // Paid outside Stripe (payment records, "paid out of band"): nothing reached our balance.
    if (!charge || charge.status !== "succeeded") continue;
    const balance = charge.balance_transaction;
    if (!balance || typeof balance === "string") {
      throw new Error(`Charge ${charge.id} has no balance transaction yet`);
    }
    settled.push({
      chargeId: charge.id,
      amount: balance.amount,
      currency: balance.currency,
      chargeAmount: charge.amount,
      chargeCurrency: charge.currency,
    });
  }
  return settled;
}

/** Record commission for a paid subscription invoice. Returns the rows it added. */
export async function recordInvoiceCommission(invoice: Stripe.Invoice, context: LedgerContext): Promise<number> {
  const { store, client, accountId } = context;
  const now = context.now ?? new Date();
  if (invoice.status !== "paid" || invoice.amount_paid <= 0 || !invoice.id) return 0;
  const subscriptionId = invoiceSubscriptionId(invoice);
  if (!subscriptionId) return 0;
  const paidAt = unixDate(invoice.status_transitions?.paid_at, now);

  let referral = await findReferral(store, subscriptionId);
  if (!referral) {
    const attribution = await resolveAttribution(invoice, context);
    if (!attribution) return 0;
    const customer = await customerUserId(invoice, store);
    if (customer === attribution.affiliateUserId) return 0;
    // A customer keeps one window per creator: a subscription moved to the US account, or a
    // new one after cancelling, continues the window of their first referred payment.
    await store.execute(
      `INSERT INTO affiliate_referrals
         (id, affiliate_user_id, customer_user_id, stripe_account_id, stripe_subscription_id, code, first_paid_at)
       SELECT $1::varchar, $2::varchar, u.id, $4::varchar, $5::varchar, $6::varchar,
              LEAST($7::timestamptz, COALESCE((SELECT MIN(r.first_paid_at) FROM affiliate_referrals r
                                   WHERE r.affiliate_user_id = $2::varchar AND r.customer_user_id = u.id), $7::timestamptz))
         FROM (SELECT 1) AS one LEFT JOIN users u ON u.id = $3::varchar
       ON CONFLICT (stripe_subscription_id) DO NOTHING`,
      [randomUUID(), attribution.affiliateUserId, customer, accountId, subscriptionId, attribution.code, paidAt],
    );
    referral = await findReferral(store, subscriptionId);
    if (!referral) return 0;
  }

  // Commission only while the creator is in the program, and only in the window.
  // Cash commission only for creators Stripe can pay (they keep Pro and their code elsewhere).
  const active = await store.query<{ status: string; country: string | null }>(
    "SELECT status, country FROM affiliates WHERE user_id = $1",
    [referral.affiliate_user_id],
  );
  if (active[0]?.status !== "approved" || !isPayoutCountry(active[0].country)) return 0;
  if (!withinCommissionWindow(new Date(referral.first_paid_at), paidAt)) return 0;

  const tax = invoiceTax(invoice);
  const availableAt = new Date(paidAt.getTime() + COMMISSION_HOLD_DAYS * DAY_MS);
  let added = 0;
  for (const charge of await settledCharges(invoice, client)) {
    // Count in the payout currency: what settled when it settled in it, else the invoice's
    // own amount when the invoice is in it (a USD price paid into a CHF balance).
    const inPayoutCurrency = charge.currency !== PAYOUT_CURRENCY && charge.chargeCurrency === PAYOUT_CURRENCY;
    const currency = inPayoutCurrency ? charge.chargeCurrency : charge.currency;
    const base = commissionBase(inPayoutCurrency ? charge.chargeAmount : charge.amount, invoice.amount_paid, tax);
    const amount = commissionAmount(base);
    if (amount <= 0) continue;
    added += await store.execute(
      `INSERT INTO affiliate_commissions
         (id, idempotency_key, affiliate_user_id, referral_id, kind, stripe_account_id, stripe_invoice_id,
          stripe_charge_id, currency, base_amount, amount, rate_bps, occurred_at, available_at)
       VALUES ($1, $2, $3, $4, 'earned', $5, $6, $7, $8, $9, $10, $11, $12, $13)
       ON CONFLICT (idempotency_key) DO NOTHING`,
      [
        randomUUID(), `earned:${charge.chargeId}`, referral.affiliate_user_id, referral.id, accountId, invoice.id,
        charge.chargeId, currency, base, amount, COMMISSION_RATE_BPS, paidAt, availableAt,
      ],
    );
  }
  return added;
}

async function chargeRows(store: SqlRunner, chargeId: string) {
  return store.query<CommissionRow & { kind: string; idempotency_key: string }>(
    `SELECT id, idempotency_key, kind, affiliate_user_id, referral_id, stripe_account_id, stripe_invoice_id,
            currency, base_amount, amount
       FROM affiliate_commissions WHERE stripe_charge_id = $1`,
    [chargeId],
  );
}

async function insertAdjustment(
  store: SqlRunner,
  earned: CommissionRow,
  chargeId: string,
  kind: "refund" | "dispute" | "dispute_won",
  key: string,
  amount: number,
  now: Date,
) {
  if (amount === 0) return 0;
  const base = earned.amount === 0 ? 0 : Math.round((earned.base_amount * amount) / earned.amount);
  return store.execute(
    `INSERT INTO affiliate_commissions
       (id, idempotency_key, affiliate_user_id, referral_id, kind, stripe_account_id, stripe_invoice_id,
        stripe_charge_id, currency, base_amount, amount, rate_bps, occurred_at, available_at)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $13)
     ON CONFLICT (idempotency_key) DO NOTHING`,
    [
      randomUUID(), key, earned.affiliate_user_id, earned.referral_id, kind, earned.stripe_account_id,
      earned.stripe_invoice_id, chargeId, earned.currency, base, amount, COMMISSION_RATE_BPS, now,
    ],
  );
}

/** A refund takes back the same share of the commission. Cumulative, so partial refunds add up. */
export async function reverseRefundedCommission(charge: Stripe.Charge, store: SqlRunner, now = new Date()) {
  if (charge.amount <= 0) return 0;
  const rows = await chargeRows(store, charge.id);
  const earned = rows.find((row) => row.kind === "earned");
  if (!earned) return 0;
  const refundedShare = Math.min(charge.amount_refunded, charge.amount) / charge.amount;
  const target = -Math.round(earned.amount * refundedShare);
  const alreadyRefunded = rows.filter((row) => row.kind === "refund").reduce((sum, row) => sum + row.amount, 0);
  const remaining = rows.reduce((sum, row) => sum + row.amount, 0);
  const delta = Math.max(target - alreadyRefunded, -remaining);
  if (delta >= 0) return 0;
  return insertAdjustment(store, earned, charge.id, "refund", `refund:${charge.id}:${charge.amount_refunded}`, delta, now);
}

/** A chargeback takes back whatever commission is left on the charge; winning it gives that back. */
export async function adjustForDispute(dispute: Stripe.Dispute, closed: boolean, store: SqlRunner, now = new Date()) {
  // Inquiries ("warning_*") don't move money.
  if (dispute.status.startsWith("warning_")) return 0;
  const chargeId = stripeObjectId(dispute.charge);
  if (!chargeId) return 0;
  const rows = await chargeRows(store, chargeId);
  const earned = rows.find((row) => row.kind === "earned");
  if (!earned) return 0;
  const reversal = rows.find((row) => row.idempotency_key === `dispute:${dispute.id}`);
  if (closed) {
    if (dispute.status !== "won" || !reversal) return 0;
    return insertAdjustment(store, earned, chargeId, "dispute_won", `dispute_won:${dispute.id}`, -reversal.amount, now);
  }
  const remaining = rows.reduce((sum, row) => sum + row.amount, 0);
  if (remaining <= 0) return 0;
  return insertAdjustment(store, earned, chargeId, "dispute", `dispute:${dispute.id}`, -remaining, now);
}

export const COMMISSION_EVENTS = [
  "invoice.paid",
  "invoice.payment_succeeded",
  "charge.refunded",
  "charge.dispute.created",
  "charge.dispute.closed",
] as const;

/**
 * Webhook entry point, called after the billing handlers. It throws on failure so Stripe
 * redelivers the event; everything it writes is idempotent.
 */
export async function handleAffiliateCommissionEvent(
  event: Stripe.Event,
  context?: Partial<LedgerContext>,
): Promise<void> {
  if (!(COMMISSION_EVENTS as readonly string[]).includes(event.type)) return;
  try {
    await applyCommissionEvent(event, context);
  } catch (error) {
    // A refused Stripe read (missing key permission, deleted object) won't succeed on
    // redelivery, and failing the webhook for days would get the endpoint disabled.
    if (error instanceof Stripe.errors.StripePermissionError || error instanceof Stripe.errors.StripeInvalidRequestError) {
      console.error(`Creator commission skipped for ${event.type} ${event.id}:`, error);
      return;
    }
    throw error;
  }
}

async function applyCommissionEvent(event: Stripe.Event, context?: Partial<LedgerContext>): Promise<void> {
  const store = context?.store ?? prismaCommissionStore();
  const now = context?.now ?? new Date();
  if (event.type === "invoice.paid" || event.type === "invoice.payment_succeeded") {
    await recordInvoiceCommission(event.data.object as Stripe.Invoice, {
      store,
      client: context?.client ?? getServerStripeClient(),
      accountId: context?.accountId ?? currentStripeAccountId(),
      now,
    });
  } else if (event.type === "charge.refunded") {
    await reverseRefundedCommission(event.data.object as Stripe.Charge, store, now);
  } else if (event.type === "charge.dispute.created" || event.type === "charge.dispute.closed") {
    await adjustForDispute(event.data.object as Stripe.Dispute, event.type === "charge.dispute.closed", store, now);
  }
}

/** Totals for the creator dashboard, per currency. */
export type EarningsTotals = {
  currency: string;
  /** Still in the hold period. */
  pending: number;
  /** Payable in the next payout. Negative after refunds of already-paid commission. */
  available: number;
  /** On its way: a transfer was started but Stripe hasn't confirmed it. */
  in_transit: number;
  paid: number;
};

export async function earningsTotals(store: SqlRunner, userId: string, now = new Date()): Promise<EarningsTotals[]> {
  return store.query<EarningsTotals>(
    `SELECT c.currency,
            COALESCE(SUM(CASE WHEN c.payout_id IS NULL AND c.available_at > $2 THEN c.amount END), 0)::int AS pending,
            COALESCE(SUM(CASE WHEN c.payout_id IS NULL AND c.available_at <= $2 THEN c.amount END), 0)::int AS available,
            COALESCE(SUM(CASE WHEN p.status = 'pending' THEN c.amount END), 0)::int AS in_transit,
            COALESCE(SUM(CASE WHEN p.status = 'paid' THEN c.amount END), 0)::int AS paid
       FROM affiliate_commissions c
       LEFT JOIN affiliate_payouts p ON p.id = c.payout_id
      WHERE c.affiliate_user_id = $1
      GROUP BY c.currency
      ORDER BY c.currency`,
    [userId, now],
  );
}

export async function referralCounts(store: SqlRunner, userId: string) {
  const rows = await store.query<{ total: number; customers: number }>(
    `SELECT COUNT(*)::int AS total, COUNT(DISTINCT customer_user_id)::int AS customers
       FROM affiliate_referrals WHERE affiliate_user_id = $1`,
    [userId],
  );
  return rows[0] ?? { total: 0, customers: 0 };
}

export type CommissionActivity = {
  occurred_at: Date;
  kind: "earned" | "refund" | "dispute" | "dispute_won";
  amount: number;
  currency: string;
  available_at: Date;
  payout_status: string | null;
};

export async function recentCommissions(store: SqlRunner, userId: string, limit = 20) {
  return store.query<CommissionActivity>(
    `SELECT c.occurred_at, c.kind, c.amount, c.currency, c.available_at, p.status AS payout_status
       FROM affiliate_commissions c
       LEFT JOIN affiliate_payouts p ON p.id = c.payout_id
      WHERE c.affiliate_user_id = $1
      ORDER BY c.occurred_at DESC, c.created_at DESC
      LIMIT ${Math.max(1, Math.min(limit, 100))}`,
    [userId],
  );
}
