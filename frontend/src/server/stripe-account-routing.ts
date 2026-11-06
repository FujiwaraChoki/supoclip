import { NextResponse } from "next/server";
import {
  BillingAccountHoldError,
  checkoutStripeAccountId,
  runWithStripeAccount,
  sourceStripeAccountId,
  stripeAccountsEnabled,
  verifiedStripeClient,
} from "@/lib/stripe-accounts";
import { resolveMigrationBillingRoute } from "@/lib/billing-migration";
import { requestMigrationCancellation } from "@/lib/stripe-migration-engine";
import { getPrismaClient } from "@/server/prisma";
import {
  createPrismaMigrationStore,
  migrationRecord,
} from "@/server/stripe-migration-store";
import type { PrismaClient } from "@/generated/prisma";
import type Stripe from "stripe";

export type BillingAction =
  "checkout" | "portal" | "resume" | "cancel" | "recovery";

/** Select the authenticated user's account; migration holds never fall back to source. */
export async function withUserBillingAccount<T>(
  userId: string,
  action: BillingAction,
  callback: () => Promise<T>,
  dependencies?: { db: PrismaClient; client(id: string): Promise<Stripe> },
): Promise<T> {
  if (!stripeAccountsEnabled()) return callback();
  const db = dependencies?.db || getPrismaClient();
  const clientFor = dependencies?.client || verifiedStripeClient;
  const user = await db.user.findUniqueOrThrow({ where: { id: userId } });
  const row = await db.stripeAccountMigration.findUnique({
    where: { user_id: userId },
  });
  let accountId =
    user.stripe_account_id ||
    (user.stripe_customer_id
      ? sourceStripeAccountId()
      : checkoutStripeAccountId());
  let recoveryOnly = false;
  let customerId = user.stripe_customer_id || undefined;
  if (row) {
    const migration = migrationRecord(row);
    if (
      action === "cancel" &&
      row.stage !== "destination_active" &&
      row.stage !== "cancelled"
    ) {
      await requestMigrationCancellation(migration, {
        store: createPrismaMigrationStore(db),
        client: clientFor,
      });
      throw new MigrationCancellationRecorded();
    }
    const route = resolveMigrationBillingRoute(migration);
    if (row.review_reason) throw new BillingAccountHoldError();
    if (route.kind === "hold") {
      if (
        action === "portal" &&
        row.recovery_required &&
        row.destination_subscription &&
        !row.cancellation_requested
      ) {
        accountId = row.destination_account;
        customerId = row.destination_customer;
        recoveryOnly = true;
      } else {
        throw new BillingAccountHoldError();
      }
    } else {
      accountId = route.reference.accountId;
      const ended = [
        "canceled",
        "inactive",
        "unpaid",
        "incomplete_expired",
      ].includes(user.subscription_status);
      if (
        user.stripe_customer_id !== route.reference.customerId ||
        (!ended &&
          user.stripe_subscription_id !== route.reference.subscriptionId)
      ) {
        throw new BillingAccountHoldError();
      }
    }
  }
  const client = await clientFor(accountId);
  return runWithStripeAccount(
    { accountId, client, recoveryOnly, customerId },
    callback,
  );
}

/** Internal control result; the caller must not execute legacy cancellation too. */
class MigrationCancellationRecorded extends Error {}

/** Convert routing holds to a recoverable response before any legacy handler runs. */
export async function billingAccountRoute(
  userId: string,
  action: BillingAction,
  callback: () => Promise<Response>,
): Promise<Response> {
  if (!stripeAccountsEnabled()) return callback();
  let invoked = false;
  try {
    return await withUserBillingAccount(userId, action, () => {
      invoked = true;
      return callback();
    });
  } catch (error) {
    if (error instanceof MigrationCancellationRecorded)
      return NextResponse.json({ ok: true });
    if (error instanceof BillingAccountHoldError)
      return NextResponse.json(
        { error: error.message, code: "billing_transfer_pending" },
        { status: 409 },
      );
    if (invoked) throw error;
    return NextResponse.json(
      { error: "Billing account is temporarily unavailable" },
      { status: 503 },
    );
  }
}
