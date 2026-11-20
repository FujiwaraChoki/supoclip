import type {
  Prisma,
  PrismaClient,
  StripeAccountMigration,
} from "@/generated/prisma";
import { parseBillingMigration } from "@/lib/billing-migration";
import type {
  MigrationCommit,
  MigrationRecord,
  MigrationStore,
} from "@/lib/stripe-migration-engine";
import {
  stripeAccountsEnabled,
  stripeAccountContext,
  sourceStripeAccountId,
} from "@/lib/stripe-accounts";
import { getPrismaClient } from "@/server/prisma";
import { getPlanIdForStripePrice } from "@/server/billing-plans";

/** Revalidate database records before they select credentials or authorize updates. */
export function migrationRecord(row: StripeAccountMigration): MigrationRecord {
  const value = parseBillingMigration({
    operationId: row.id,
    appUserId: row.user_id,
    source: {
      accountId: row.source_account,
      customerId: row.source_customer,
      subscriptionId: row.source_subscription,
    },
    ...(row.destination_subscription || row.destination_schedule
      ? {
          destination: {
            accountId: row.destination_account,
            customerId: row.destination_customer,
            ...(row.destination_subscription
              ? { subscriptionId: row.destination_subscription }
              : {}),
            ...(row.destination_schedule
              ? { scheduleId: row.destination_schedule }
              : {}),
          },
        }
      : {}),
    stage: row.stage,
    sourcePaidThrough: row.paid_through.getTime() / 1000,
    sourceStoppedVerified: row.source_stopped_verified,
    destinationPaidInvoiceVerified: Boolean(row.destination_paid_invoice),
    sourceResumedVerified: row.source_resumed_verified,
    destinationDisabledVerified: row.destination_disabled_verified,
    customerCancellationRequested: row.cancellation_requested,
    complimentary: false,
    livemode: row.livemode,
  });
  return {
    ...value,
    revision: row.revision,
    recoveryRequired: row.recovery_required,
    ...(row.review_reason ? { reviewReason: row.review_reason } : {}),
  };
}

/** Always lock the user before the ledger, including competing cancellation calls. */
async function lockMigration(
  tx: Prisma.TransactionClient,
  id: string,
  userId: string,
): Promise<StripeAccountMigration> {
  await tx.$queryRaw`SELECT id FROM users WHERE id = ${userId} FOR UPDATE`;
  const row = await tx.stripeAccountMigration.findUnique({ where: { id } });
  if (!row || row.user_id !== userId)
    throw new Error("Migration owner changed");
  return row;
}

/** Database-backed adapter: no email, transport, or live Stripe mutation. */
export function createPrismaMigrationStore(db: PrismaClient): MigrationStore {
  return {
    async byCustomer(accountId, customerId) {
      const rows = await db.stripeAccountMigration.findMany({
        where: {
          OR: [
            { source_account: accountId, source_customer: customerId },
            {
              destination_account: accountId,
              destination_customer: customerId,
            },
          ],
        },
        take: 2,
      });
      if (rows.length > 1)
        throw new Error("Ambiguous account-qualified migration customer");
      return rows[0] ? migrationRecord(rows[0]) : null;
    },
    async requestCancellation(operationId, userId) {
      return db.$transaction(async (tx) => {
        const row = await lockMigration(tx, operationId, userId);
        if (row.cancellation_requested) return migrationRecord(row);
        const updated = await tx.stripeAccountMigration.update({
          where: { id: row.id },
          data: {
            cancellation_requested: true,
            revision: { increment: 1 },
          },
        });
        await tx.user.update({
          where: { id: userId },
          data: {
            subscription_cancel_at:
              row.stage === "destination_active"
                ? (await tx.user.findUniqueOrThrow({ where: { id: userId } }))
                    .billing_period_end
                : row.paid_through,
          },
        });
        return migrationRecord(updated);
      });
    },
    async finishCancellation(expected) {
      await db.$transaction(async (tx) => {
        const row = await lockMigration(
          tx,
          expected.operationId,
          expected.appUserId,
        );
        if (row.revision !== expected.revision || !row.cancellation_requested)
          throw new Error("Cancellation changed; retry verification");
        await tx.stripeAccountMigration.update({
          where: { id: row.id },
          data: {
            ...(row.stage !== "destination_active"
              ? { stage: "cancelled", destination_disabled_verified: true }
              : {}),
            revision: { increment: 1 },
          },
        });
      });
    },
    async commit(expected, change) {
      await db.$transaction(async (tx) => {
        const row = await lockMigration(
          tx,
          expected.operationId,
          expected.appUserId,
        );
        if (row.revision !== expected.revision)
          throw new Error("Migration changed; re-read Stripe and retry");
        if (change.kind === "review") {
          await tx.stripeAccountMigration.update({
            where: { id: row.id },
            data: { review_reason: change.reason, revision: { increment: 1 } },
          });
          return;
        }
        await applyVerifiedSnapshot(tx, row, change);
      });
    },
  };
}

/** Apply a fresh snapshot only while the same operation still owns this user. */
async function applyVerifiedSnapshot(
  tx: Prisma.TransactionClient,
  row: StripeAccountMigration,
  change: Exclude<MigrationCommit, { kind: "review" }>,
): Promise<void> {
  const snapshot = change.snapshot;
  const current = migrationRecord(row);
  const source =
    snapshot.accountId === row.source_account &&
    snapshot.customerId === row.source_customer &&
    snapshot.subscriptionId === row.source_subscription;
  const destination =
    snapshot.accountId === row.destination_account &&
    snapshot.customerId === row.destination_customer &&
    (snapshot.subscriptionId === row.destination_subscription ||
      (!row.destination_subscription &&
        snapshot.scheduleId === row.destination_schedule));
  if (
    row.review_reason ||
    (row.cancellation_requested && change.kind === "activate")
  )
    throw new Error("Migration is held or cancelled");
  if (change.kind === "activate") {
    if (
      !destination ||
      !row.source_stopped_verified ||
      !snapshot.paidInvoiceId ||
      snapshot.status !== "active" ||
      snapshot.periodStart !== current.sourcePaidThrough
    ) {
      throw new Error("Destination activation proof is incomplete");
    }
  } else if (change.kind === "payment_failed") {
    if (!destination || !row.source_stopped_verified)
      throw new Error("Unowned migration payment failure");
  } else if (
    !(
      source &&
      (row.stage === "prepared" || row.stage === "rollback_verified")
    ) &&
    !(destination && row.stage === "destination_active")
  ) {
    throw new Error("Superseded subscription cannot update this user");
  }
  const user = await tx.user.findUniqueOrThrow({ where: { id: row.user_id } });
  const expectedAccount =
    row.stage === "destination_active"
      ? row.destination_account
      : row.source_account;
  const expectedSub =
    row.stage === "destination_active"
      ? row.destination_subscription
      : row.source_subscription;
  const expectedCustomer =
    row.stage === "destination_active"
      ? row.destination_customer
      : row.source_customer;
  if (
    (user.stripe_account_id || row.source_account) !== expectedAccount ||
    user.stripe_subscription_id !== expectedSub ||
    user.stripe_customer_id !== expectedCustomer
  ) {
    throw new Error("App billing owner changed during migration");
  }
  const plan = getPlanIdForStripePrice(snapshot.priceId);
  if (!plan)
    throw new Error("Destination price has no verified app plan alias");
  const ledgerData = {
    revision: { increment: 1 },
    ...(destination
      ? { destination_subscription: snapshot.subscriptionId }
      : {}),
  };
  if (change.kind === "payment_failed") {
    await tx.stripeAccountMigration.update({
      where: { id: row.id },
      data: { ...ledgerData, recovery_required: true },
    });
    if (user.subscription_provider === "stripe") {
      await tx.user.update({
        where: { id: user.id },
        data: { subscription_status: "past_due" },
      });
    }
    return;
  }
  const entitled = ["active", "trialing", "past_due"].includes(snapshot.status);
  const alternateGrant =
    user.subscription_provider === "apple" ||
    user.subscription_provider === "affiliate";
  await tx.user.update({
    where: { id: user.id },
    data: {
      stripe_account_id: snapshot.accountId,
      stripe_customer_id: snapshot.customerId,
      stripe_subscription_id: snapshot.subscriptionId,
      ...(!alternateGrant
        ? {
            ...(change.kind === "activate"
              ? { pending_plan: null, pending_plan_invoice_id: null }
              : {}),
            plan: entitled ? plan : "free",
            subscription_provider: entitled ? "stripe" : null,
            subscription_status: snapshot.status,
            billing_period_start: new Date(snapshot.periodStart * 1000),
            billing_period_end: new Date(snapshot.periodEnd * 1000),
            subscription_cancel_at: snapshot.cancelAt
              ? new Date(snapshot.cancelAt * 1000)
              : null,
            ...(snapshot.status === "canceled" ? { legacy_hd: false } : {}),
          }
        : {}),
    },
  });
  await tx.stripeAccountMigration.update({
    where: { id: row.id },
    data: {
      ...ledgerData,
      ...(change.kind === "activate"
        ? {
            stage: "destination_active",
            destination_paid_invoice: snapshot.paidInvoiceId,
            recovery_required: false,
          }
        : {}),
    },
  });
}

/** Protect ordinary sync writers against a cutover occurring after the webhook lookup. */
export async function guardedBillingUpdate(
  customerId: string,
  subscriptionId: string,
  where: Prisma.UserWhereInput,
  data: Prisma.UserUpdateManyMutationInput,
): Promise<{ count: number }> {
  const db = getPrismaClient();
  if (!stripeAccountsEnabled()) return db.user.updateMany({ where, data });
  const account = stripeAccountContext()?.accountId;
  if (!account) throw new Error("Missing Stripe account for database update");
  if (!subscriptionId) return { count: 0 };
  return db.$transaction(async (tx) => {
    const users = await tx.$queryRaw<
      Array<{ id: string }>
    >`SELECT id FROM users WHERE stripe_customer_id = ${customerId} FOR UPDATE`;
    if (users.length !== 1) return { count: 0 };
    const user = await tx.user.findUniqueOrThrow({
      where: { id: users[0].id },
    });
    if ((user.stripe_account_id || sourceStripeAccountId()) !== account)
      return { count: 0 };
    const ledger = await tx.stripeAccountMigration.findUnique({
      where: { user_id: user.id },
    });
    if (ledger) {
      const current = migrationRecord(ledger);
      const owner =
        current.stage === "destination_active"
          ? current.destination
          : current.source;
      if (
        ![
          "prepared",
          "rollback_verified",
          "cancelled",
          "destination_active",
        ].includes(current.stage)
      )
        return { count: 0 };
      if (
        owner?.accountId !== account ||
        owner.subscriptionId !== subscriptionId
      )
        return { count: 0 };
      if (
        user.subscription_provider === "apple" ||
        user.subscription_provider === "affiliate"
      ) {
        const rank: Record<string, number> = { free: 0, pro: 1, scale: 2 };
        if (
          typeof data.plan !== "string" ||
          (rank[data.plan] ?? 0) <= (rank[user.plan] ?? 0)
        )
          return { count: 0 };
      }
    } else if (user.stripe_subscription_id !== subscriptionId) {
      return { count: 0 };
    }
    return tx.user.updateMany({
      where: { AND: [where, { id: user.id }] },
      data: { ...data, stripe_account_id: account },
    });
  });
}

/** Checkout may bind only an already assigned customer, never a matching email. */
export async function bindAccountCheckout(
  userId: string,
  customerId: string,
  subscriptionId: string,
): Promise<boolean> {
  const db = getPrismaClient();
  const account = stripeAccountContext()?.accountId;
  if (!account) throw new Error("Missing checkout account context");
  return db.$transaction(async (tx) => {
    await tx.$queryRaw`SELECT id FROM users WHERE id = ${userId} FOR UPDATE`;
    const user = await tx.user.findUnique({ where: { id: userId } });
    if (
      !user ||
      user.stripe_customer_id !== customerId ||
      (user.stripe_account_id || sourceStripeAccountId()) !== account
    )
      return false;
    const migration = await tx.stripeAccountMigration.findUnique({
      where: { user_id: userId },
    });
    const settledDestination =
      migration?.stage === "destination_active" &&
      migration.destination_account === account &&
      migration.destination_customer === customerId;
    const settledSource =
      migration?.stage === "cancelled" &&
      migration.destination_disabled_verified &&
      migration.source_account === account &&
      migration.source_customer === customerId;
    if (
      migration &&
      ((!settledDestination && !settledSource) || migration.review_reason)
    )
      return false;
    if (
      user.stripe_subscription_id &&
      user.stripe_subscription_id !== subscriptionId &&
      ["active", "trialing", "past_due"].includes(user.subscription_status)
    )
      return false;
    await tx.user.update({
      where: { id: userId },
      data: {
        stripe_subscription_id: subscriptionId,
        stripe_account_id: account,
      },
    });
    if (
      migration &&
      (settledDestination
        ? migration.destination_subscription
        : migration.source_subscription) !== subscriptionId
    ) {
      await tx.stripeAccountMigration.update({
        where: { id: migration.id },
        data: {
          ...(settledDestination
            ? { destination_subscription: subscriptionId }
            : { source_subscription: subscriptionId }),
          revision: { increment: 1 },
        },
      });
    }
    return true;
  });
}
