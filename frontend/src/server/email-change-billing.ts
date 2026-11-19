import Stripe from "stripe";

import type { PrismaClient } from "@/generated/prisma";
import { getStripeClient } from "@/lib/stripe";
import { sourceStripeAccountId, stripeAccountsEnabled, verifiedStripeClient } from "@/lib/stripe-accounts";

/** A Stripe customer that sends this user receipts. `accountId` is null for the single-account setup. */
export interface BillingCustomerRef {
  accountId: string | null;
  customerId: string;
}

type BillingDb = Pick<PrismaClient, "user" | "stripeAccountMigration">;

/**
 * Every customer that may email the user: the one on the user row (on its
 * account, or the legacy source account) and, during an account migration,
 * both the source and destination customers.
 */
export async function billingCustomerRefs(db: BillingDb, userId: string): Promise<BillingCustomerRef[]> {
  const user = await db.user.findUnique({
    where: { id: userId },
    select: { stripe_customer_id: true, stripe_account_id: true },
  });
  if (!stripeAccountsEnabled()) {
    return user?.stripe_customer_id ? [{ accountId: null, customerId: user.stripe_customer_id }] : [];
  }
  const refs: BillingCustomerRef[] = [];
  const add = (accountId: string | null | undefined, customerId: string | null | undefined) => {
    if (!accountId || !customerId) return;
    if (!refs.some((ref) => ref.accountId === accountId && ref.customerId === customerId)) {
      refs.push({ accountId, customerId });
    }
  };
  if (user?.stripe_customer_id) {
    add(user.stripe_account_id || sourceStripeAccountId(), user.stripe_customer_id);
  }
  const migration = await db.stripeAccountMigration.findUnique({
    where: { user_id: userId },
    select: { source_account: true, source_customer: true, destination_account: true, destination_customer: true },
  });
  if (migration) {
    add(migration.source_account, migration.source_customer);
    add(migration.destination_account, migration.destination_customer);
  }
  return refs;
}

function isMissingCustomer(error: unknown) {
  return error instanceof Stripe.errors.StripeInvalidRequestError && error.code === "resource_missing";
}

export interface BillingEmailDeps {
  refs(userId: string): Promise<BillingCustomerRef[]>;
  client(accountId: string | null): Promise<Pick<Stripe, "customers">>;
}

/**
 * Points each customer at `to`. All or nothing: on a failure the customers
 * already moved get their previous address back and the error is rethrown.
 * Returns an undo for when the account update after it fails. A deleted
 * customer is skipped.
 */
export async function updateBillingEmail(
  deps: BillingEmailDeps,
  userId: string,
  to: string,
): Promise<() => Promise<void>> {
  // `previous` is what Stripe had, which may differ from the sign-in address.
  const moved: { ref: BillingCustomerRef; client: Pick<Stripe, "customers">; previous: string }[] = [];
  const undo = async () => {
    const results = await Promise.allSettled(
      moved.map(({ ref, client, previous }) => client.customers.update(ref.customerId, { email: previous })),
    );
    const failed = results.filter((result) => result.status === "rejected");
    if (failed.length > 0) throw new Error(`Could not restore ${failed.length} billing customer email(s)`);
  };

  try {
    for (const ref of await deps.refs(userId)) {
      const client = await deps.client(ref.accountId);
      try {
        const customer = await client.customers.retrieve(ref.customerId);
        if (customer.deleted) continue;
        await client.customers.update(ref.customerId, { email: to });
        // Stripe clears the field when given "".
        moved.push({ ref, client, previous: customer.email ?? "" });
      } catch (error) {
        if (isMissingCustomer(error)) continue;
        throw error;
      }
    }
  } catch (error) {
    await undo().catch((undoError) => console.error("Email change: billing email rollback failed", undoError));
    throw error;
  }
  return undo;
}

export function liveBillingEmailDeps(db: BillingDb): BillingEmailDeps {
  return {
    refs: (userId) => billingCustomerRefs(db, userId),
    client: async (accountId) => (accountId ? verifiedStripeClient(accountId) : getStripeClient()),
  };
}
