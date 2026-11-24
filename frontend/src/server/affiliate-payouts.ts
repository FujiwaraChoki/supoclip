import { randomUUID } from "node:crypto";
import Stripe from "stripe";

import { MINIMUM_PAYOUT_MINOR, type PayoutCountry } from "@/lib/creator-commission";
import { getStripeClient } from "@/lib/stripe";
import { checkoutStripeAccountId, stripeAccountsEnabled, verifiedStripeClient } from "@/lib/stripe-accounts";
import type { CommissionStore, SqlRunner } from "@/server/affiliate-commissions";

/**
 * Paying creators through Stripe Connect Express.
 *
 * A creator connects an Express account once (Stripe collects identity, tax and bank
 * details). An admin then pays everyone from /admin: each creator's commission that has
 * passed its hold is claimed into one `affiliate_payouts` row and sent as one Transfer
 * from the platform account's balance. The transfer's idempotency key and transfer_group
 * are the payout ID, so a retried or interrupted payout can never pay twice.
 */

/**
 * The account creators' Express accounts belong to and transfers are paid from: by default
 * the account new checkouts open in (the US account in production), where revenue lands.
 */
export function connectPlatformAccountId(): string {
  if (process.env.STRIPE_CONNECT_PLATFORM_ACCOUNT_ID) return process.env.STRIPE_CONNECT_PLATFORM_ACCOUNT_ID;
  if (stripeAccountsEnabled()) return checkoutStripeAccountId();
  return process.env.STRIPE_SOURCE_ACCOUNT_ID || "default";
}

export async function connectPlatformClient(): Promise<Stripe> {
  if (stripeAccountsEnabled()) return verifiedStripeClient(connectPlatformAccountId());
  return getStripeClient();
}

export type ConnectAffiliate = {
  user_id: string;
  slug: string | null;
  status: string;
  stripe_connect_account_id: string | null;
  stripe_connect_platform_id: string | null;
  payout_country: string | null;
  payouts_enabled: boolean;
};

export function canReceivePayouts(account: Stripe.Account): boolean {
  return Boolean(account.payouts_enabled && account.capabilities?.transfers === "active");
}

/**
 * What a creator's Express account asks Stripe for. From the US platform, a US account can
 * take transfers alone; an account in another country must also request card payments (the
 * recipient agreement isn't available to US platforms there). Checked against live Stripe on
 * 24 Nov 2026 for US, DE, GB, CA and CH.
 */
export function connectCapabilities(country: PayoutCountry): Stripe.AccountCreateParams.Capabilities {
  return country === "US"
    ? { transfers: { requested: true } }
    : { transfers: { requested: true }, card_payments: { requested: true } };
}

/** The creator's Express account, created on first use in the country they picked. */
export async function ensureConnectAccount(
  affiliate: ConnectAffiliate,
  input: { country: PayoutCountry; email: string; profileUrl: string },
  { store, client }: { store: SqlRunner; client: Stripe },
): Promise<string> {
  const platformId = connectPlatformAccountId();
  if (affiliate.stripe_connect_account_id && affiliate.stripe_connect_platform_id === platformId) {
    return affiliate.stripe_connect_account_id;
  }
  const account = await client.accounts.create(
    {
      type: "express",
      country: input.country,
      email: input.email,
      capabilities: connectCapabilities(input.country),
      business_profile: {
        url: input.profileUrl,
        product_description: "Creates videos that promote SupoClip and earns referral commission.",
      },
      metadata: { supoclip_user_id: affiliate.user_id, supoclip_creator_code: affiliate.slug ?? "" },
    },
    { idempotencyKey: `creator-connect-${affiliate.user_id}-${platformId}` },
  );
  await store.execute(
    `UPDATE affiliates
        SET stripe_connect_account_id = $2, stripe_connect_platform_id = $3, payout_country = $4,
            payouts_enabled = false, updated_at = now()
      WHERE user_id = $1`,
    [affiliate.user_id, account.id, platformId, input.country],
  );
  return account.id;
}

export async function onboardingLink(client: Stripe, accountId: string, appUrl: string): Promise<string> {
  const link = await client.accountLinks.create({
    account: accountId,
    type: "account_onboarding",
    refresh_url: `${appUrl}/settings/creator-program?payouts=refresh`,
    return_url: `${appUrl}/settings/creator-program?payouts=return`,
  });
  return link.url;
}

export type ConnectStatus = "not_started" | "incomplete" | "enabled";

/** Read the creator's account from Stripe and store whether it can be paid. */
export async function refreshConnectStatus(
  affiliate: ConnectAffiliate,
  { store, client }: { store: SqlRunner; client: Stripe },
): Promise<{ status: ConnectStatus; detailsSubmitted: boolean }> {
  if (!affiliate.stripe_connect_account_id || affiliate.stripe_connect_platform_id !== connectPlatformAccountId()) {
    return { status: "not_started", detailsSubmitted: false };
  }
  const account = await client.accounts.retrieve(affiliate.stripe_connect_account_id);
  const enabled = canReceivePayouts(account);
  if (enabled !== affiliate.payouts_enabled) {
    await store.execute("UPDATE affiliates SET payouts_enabled = $2, updated_at = now() WHERE user_id = $1", [
      affiliate.user_id,
      enabled,
    ]);
  }
  return { status: enabled ? "enabled" : "incomplete", detailsSubmitted: Boolean(account.details_submitted) };
}

export type PayableBalance = {
  user_id: string;
  name: string | null;
  email: string;
  slug: string | null;
  currency: string;
  available: number;
  pending: number;
  payouts_enabled: boolean;
  stripe_connect_account_id: string | null;
};

/** Every approved creator's balance per currency, for the admin panel and the payout run. */
export async function creatorBalances(store: SqlRunner, now = new Date()): Promise<PayableBalance[]> {
  return store.query<PayableBalance>(
    `SELECT a.user_id, u.name, u.email, a.slug, c.currency,
            COALESCE(SUM(CASE WHEN c.available_at <= $1 THEN c.amount END), 0)::int AS available,
            COALESCE(SUM(CASE WHEN c.available_at > $1 THEN c.amount END), 0)::int AS pending,
            (a.payouts_enabled AND a.stripe_connect_platform_id = $2) AS payouts_enabled,
            a.stripe_connect_account_id
       FROM affiliate_commissions c
       JOIN affiliates a ON a.user_id = c.affiliate_user_id
       JOIN users u ON u.id = a.user_id
      WHERE c.payout_id IS NULL AND a.status = 'approved'
      GROUP BY a.user_id, u.name, u.email, a.slug, c.currency, a.payouts_enabled,
               a.stripe_connect_platform_id, a.stripe_connect_account_id
      ORDER BY available DESC`,
    [now, connectPlatformAccountId()],
  );
}

type Claim = { payoutId: string; amount: number };

/**
 * Move every commission that has passed its hold into a new payout row, in one
 * transaction. Returns null (and claims nothing) when the total is under the minimum.
 */
export async function claimPayout(
  store: CommissionStore,
  input: { userId: string; currency: string; connectAccountId: string; adminId: string | null; now?: Date },
): Promise<Claim | null> {
  const now = input.now ?? new Date();
  return store
    .transaction(async (tx) => {
      // One claim per creator at a time.
      await tx.query("SELECT user_id FROM affiliates WHERE user_id = $1 FOR UPDATE", [input.userId]);
      const payoutId = randomUUID();
      // Placeholder amount; set below from exactly the rows this statement claims.
      await tx.execute(
        `INSERT INTO affiliate_payouts
           (id, affiliate_user_id, currency, amount, stripe_platform_id, stripe_connect_account_id, status, created_by)
         VALUES ($1, $2, $3, 1, $4, $5, 'pending', $6)`,
        [payoutId, input.userId, input.currency, connectPlatformAccountId(), input.connectAccountId, input.adminId],
      );
      const claimed = await tx.query<{ amount: number }>(
        `UPDATE affiliate_commissions SET payout_id = $1
          WHERE affiliate_user_id = $2 AND currency = $3 AND payout_id IS NULL AND available_at <= $4
          RETURNING amount`,
        [payoutId, input.userId, input.currency, now],
      );
      const amount = claimed.reduce((sum, row) => sum + Number(row.amount), 0);
      if (amount < MINIMUM_PAYOUT_MINOR) throw new BelowMinimum();
      await tx.execute("UPDATE affiliate_payouts SET amount = $2 WHERE id = $1", [payoutId, amount]);
      return { payoutId, amount };
    })
    .catch((error) => {
      if (error instanceof BelowMinimum) return null;
      throw error;
    });
}

class BelowMinimum extends Error {}

/** Errors that mean Stripe refused the transfer, so retrying the same request can't help. */
function isDefinitiveFailure(error: unknown): boolean {
  if (!(error instanceof Stripe.errors.StripeError)) return false;
  const status = error.statusCode ?? 0;
  return status >= 400 && status < 500 && status !== 409 && status !== 429;
}

async function markPaid(store: SqlRunner, payoutId: string, transferId: string) {
  await store.execute(
    "UPDATE affiliate_payouts SET status = 'paid', stripe_transfer_id = $2, failure_reason = NULL, updated_at = now() WHERE id = $1",
    [payoutId, transferId],
  );
}

/** A refused payout gives its commissions back to the balance for the next run. */
async function markFailed(store: CommissionStore, payoutId: string, reason: string) {
  await store.transaction(async (tx) => {
    await tx.execute("UPDATE affiliate_commissions SET payout_id = NULL WHERE payout_id = $1", [payoutId]);
    await tx.execute(
      "UPDATE affiliate_payouts SET status = 'failed', failure_reason = $2, updated_at = now() WHERE id = $1",
      [payoutId, reason.slice(0, 500)],
    );
  });
}

export type PayoutResult = {
  user_id: string;
  currency: string;
  amount: number;
  outcome: "paid" | "failed" | "pending" | "skipped";
  reason?: string;
};

/** Send one claimed payout. Safe to call again for the same payout. */
export async function sendPayout(
  store: CommissionStore,
  client: Stripe,
  payout: { id: string; affiliate_user_id: string; currency: string; amount: number; stripe_connect_account_id: string },
): Promise<PayoutResult> {
  const base = { user_id: payout.affiliate_user_id, currency: payout.currency, amount: payout.amount };
  try {
    // Idempotency keys expire after a day; the transfer group finds an older success.
    const existing = await client.transfers.list({ transfer_group: payout.id, limit: 1 });
    const transfer =
      existing.data[0] ??
      (await client.transfers.create(
        {
          amount: payout.amount,
          currency: payout.currency,
          destination: payout.stripe_connect_account_id,
          transfer_group: payout.id,
          description: "SupoClip creator commission",
          metadata: { supoclip_payout_id: payout.id, supoclip_user_id: payout.affiliate_user_id },
        },
        { idempotencyKey: `creator-payout-${payout.id}` },
      ));
    await markPaid(store, payout.id, transfer.id);
    return { ...base, outcome: "paid" };
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    if (isDefinitiveFailure(error)) {
      await markFailed(store, payout.id, reason);
      return { ...base, outcome: "failed", reason };
    }
    // Unknown outcome (network, Stripe 5xx): stays pending and is retried on the next run.
    return { ...base, outcome: "pending", reason };
  }
}

/**
 * Pay every creator whose available balance reaches the minimum. Payouts left pending
 * by an earlier interrupted run are finished first.
 */
export async function runCreatorPayouts(
  store: CommissionStore,
  client: Stripe,
  { adminId, now = new Date() }: { adminId: string | null; now?: Date },
): Promise<PayoutResult[]> {
  const results: PayoutResult[] = [];
  const pending = await store.query<{
    id: string; affiliate_user_id: string; currency: string; amount: number; stripe_connect_account_id: string;
  }>(
    `SELECT id, affiliate_user_id, currency, amount, stripe_connect_account_id
       FROM affiliate_payouts WHERE status = 'pending' AND stripe_platform_id = $1 ORDER BY created_at`,
    [connectPlatformAccountId()],
  );
  for (const payout of pending) results.push(await sendPayout(store, client, payout));

  const balances = await creatorBalances(store, now);
  const affiliates = new Map<string, ConnectAffiliate>();
  for (const balance of balances) {
    if (balance.available < MINIMUM_PAYOUT_MINOR) continue;
    const skipped = (reason: string) =>
      results.push({ user_id: balance.user_id, currency: balance.currency, amount: balance.available, outcome: "skipped", reason });
    if (!balance.stripe_connect_account_id) {
      skipped("Payouts not set up");
      continue;
    }
    // Re-check with Stripe: an account can be restricted since it was last seen.
    if (!affiliates.has(balance.user_id)) {
      const rows = await store.query<ConnectAffiliate>(
        `SELECT user_id, slug, status, stripe_connect_account_id, stripe_connect_platform_id, payout_country, payouts_enabled
           FROM affiliates WHERE user_id = $1`,
        [balance.user_id],
      );
      const affiliate = rows[0];
      const { status } = await refreshConnectStatus(affiliate, { store, client });
      affiliates.set(balance.user_id, { ...affiliate, payouts_enabled: status === "enabled" });
    }
    const affiliate = affiliates.get(balance.user_id)!;
    if (!affiliate.payouts_enabled || !affiliate.stripe_connect_account_id) {
      skipped("Stripe account can't receive payouts yet");
      continue;
    }
    const claim = await claimPayout(store, {
      userId: balance.user_id,
      currency: balance.currency,
      connectAccountId: affiliate.stripe_connect_account_id,
      adminId,
      now,
    });
    if (!claim) continue;
    results.push(
      await sendPayout(store, client, {
        id: claim.payoutId,
        affiliate_user_id: balance.user_id,
        currency: balance.currency,
        amount: claim.amount,
        stripe_connect_account_id: affiliate.stripe_connect_account_id,
      }),
    );
  }
  return results;
}

export async function recentPayouts(store: SqlRunner, userId: string, limit = 10) {
  return store.query<{ created_at: Date; amount: number; currency: string; status: string }>(
    `SELECT created_at, amount, currency, status FROM affiliate_payouts
      WHERE affiliate_user_id = $1 ORDER BY created_at DESC LIMIT ${Math.max(1, Math.min(limit, 50))}`,
    [userId],
  );
}
