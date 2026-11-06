import type Stripe from "stripe";
import {
  parseBillingMigration,
  type BillingMigration,
} from "@/lib/billing-migration";

export type MigrationRecord = BillingMigration & {
  revision: number;
  recoveryRequired: boolean;
  reviewReason?: string;
};
export type VerifiedBillingSnapshot = {
  accountId: string;
  customerId: string;
  subscriptionId: string;
  scheduleId?: string;
  status: Stripe.Subscription.Status;
  priceId: string;
  periodStart: number;
  periodEnd: number;
  cancelAt: number | null;
  paidInvoiceId?: string;
};
export type MigrationCommit =
  | {
      kind: "activate" | "refresh" | "payment_failed";
      snapshot: VerifiedBillingSnapshot;
    }
  | { kind: "review"; reason: string };
export interface MigrationStore {
  byCustomer(
    accountId: string,
    customerId: string,
  ): Promise<MigrationRecord | null>;
  commit(expected: MigrationRecord, change: MigrationCommit): Promise<void>;
  requestCancellation(
    operationId: string,
    userId: string,
  ): Promise<MigrationRecord>;
  finishCancellation(record: MigrationRecord): Promise<void>;
}
export type MigrationEngineDependencies = {
  store: MigrationStore;
  client(accountId: string): Promise<Stripe>;
  dispatchCurrent?(
    event: Stripe.Event,
    subscription: Stripe.Subscription,
  ): Promise<void>;
};
export const MIGRATION_OPERATION_METADATA = "billing_migration_operation";

/** Read an object ID without ever falling back to an email or caller metadata. */
export function stripeObjectId(value: unknown): string | undefined {
  if (typeof value === "string") return value;
  if (
    value &&
    typeof value === "object" &&
    "id" in value &&
    typeof value.id === "string"
  )
    return value.id;
  return undefined;
}

/** Compatibility boundary for invoice versions before and after parent.subscription_details. */
export function invoiceSubscriptionId(
  invoice: Stripe.Invoice,
): string | undefined {
  const legacy = invoice as Stripe.Invoice & { subscription?: unknown };
  return (
    stripeObjectId(invoice.parent?.subscription_details?.subscription) ||
    stripeObjectId(legacy.subscription)
  );
}

/** Require a single, fully retrieved subscription item; complex imports stay on hold. */
export function subscriptionSnapshot(
  accountId: string,
  subscription: Stripe.Subscription,
): VerifiedBillingSnapshot {
  if (subscription.items.has_more || subscription.items.data.length !== 1)
    throw new Error("Unverified subscription items");
  const item = subscription.items.data[0];
  const customerId = stripeObjectId(subscription.customer);
  if (
    !customerId ||
    !item.current_period_start ||
    !item.current_period_end ||
    !item.price.id
  )
    throw new Error("Incomplete billing snapshot");
  return {
    accountId,
    customerId,
    subscriptionId: subscription.id,
    ...(stripeObjectId(subscription.schedule)
      ? { scheduleId: stripeObjectId(subscription.schedule) }
      : {}),
    status: subscription.status,
    priceId: item.price.id,
    periodStart: item.current_period_start,
    periodEnd: item.current_period_end,
    cancelAt:
      subscription.cancel_at ??
      (subscription.cancel_at_period_end ? item.current_period_end : null),
  };
}

/** Destination cancellation is retried with a stable operation identity. */
export async function cancelMigrationReplacement(
  record: MigrationRecord,
  dependencies: MigrationEngineDependencies,
): Promise<void> {
  const destination = record.destination;
  if (!destination) return;
  const client = await dependencies.client(destination.accountId);
  const key = `migration:${record.operationId}:customer-cancel:${record.revision}`;
  if (record.stage === "destination_active" && destination.subscriptionId) {
    const current = await client.subscriptions.retrieve(
      destination.subscriptionId,
    );
    if (stripeObjectId(current.customer) !== destination.customerId)
      throw new Error("Destination cancellation owner mismatch");
    if (current.status !== "canceled" && !current.cancel_at_period_end) {
      await client.subscriptions.update(
        current.id,
        { cancel_at_period_end: true },
        { idempotencyKey: key + ":subscription" },
      );
    }
  } else if (destination.scheduleId) {
    const schedule = await client.subscriptionSchedules.retrieve(
      destination.scheduleId,
    );
    if (stripeObjectId(schedule.customer) !== destination.customerId)
      throw new Error("Destination schedule owner mismatch");
    if (schedule.status === "not_started" || schedule.status === "active") {
      await client.subscriptionSchedules.cancel(
        schedule.id,
        { invoice_now: false, prorate: false },
        { idempotencyKey: key + ":schedule" },
      );
    }
  } else if (destination.subscriptionId) {
    const current = await client.subscriptions.retrieve(
      destination.subscriptionId,
    );
    if (stripeObjectId(current.customer) !== destination.customerId)
      throw new Error("Destination cancellation owner mismatch");
    if (current.status !== "canceled") {
      await client.subscriptions.cancel(
        current.id,
        { invoice_now: false, prorate: false },
        { idempotencyKey: key + ":pending" },
      );
    }
  }
}

/** Persist intent before API calls, so partial failures cannot reactivate a replacement. */
export async function requestMigrationCancellation(
  record: MigrationRecord,
  dependencies: MigrationEngineDependencies,
): Promise<void> {
  const current = await dependencies.store.requestCancellation(
    record.operationId,
    record.appUserId,
  );
  await cancelMigrationReplacement(current, dependencies);
  const source = await dependencies.client(current.source.accountId);
  const sub = await source.subscriptions.retrieve(
    current.source.subscriptionId,
  );
  if (stripeObjectId(sub.customer) !== current.source.customerId)
    throw new Error("Source cancellation owner mismatch");
  if (sub.status !== "canceled" && !sub.cancel_at_period_end) {
    await source.subscriptions.update(
      sub.id,
      { cancel_at_period_end: true },
      { idempotencyKey: `migration:${record.operationId}:source-user-cancel` },
    );
  }
  const verifiedSource = await source.subscriptions.retrieve(
    current.source.subscriptionId,
  );
  if (
    verifiedSource.status !== "canceled" &&
    !verifiedSource.cancel_at_period_end
  )
    throw new Error("Source cancellation read-back failed");
  if (current.destination) {
    const destination = await dependencies.client(
      current.destination.accountId,
    );
    if (
      current.destination.scheduleId &&
      current.stage !== "destination_active"
    ) {
      const schedule = await destination.subscriptionSchedules.retrieve(
        current.destination.scheduleId,
      );
      if (schedule.status !== "canceled")
        throw new Error("Destination schedule is not stopped");
    } else if (current.destination.subscriptionId) {
      const verified = await destination.subscriptions.retrieve(
        current.destination.subscriptionId,
      );
      if (
        verified.status !== "canceled" &&
        !(
          current.stage === "destination_active" &&
          verified.cancel_at_period_end
        )
      )
        throw new Error("Destination cancellation read-back failed");
    }
  }
  await dependencies.store.finishCancellation(current);
}

/** A destination may take over only while the legacy renewal is verifiably stopped. */
async function assertSourceStopped(
  record: MigrationRecord,
  dependencies: MigrationEngineDependencies,
): Promise<void> {
  if (!record.sourceStoppedVerified)
    throw new Error("Source stop is not verified");
  const source = await dependencies.client(record.source.accountId);
  const current = await source.subscriptions.retrieve(
    record.source.subscriptionId,
  );
  const snapshot = subscriptionSnapshot(record.source.accountId, current);
  if (
    snapshot.customerId !== record.source.customerId ||
    (current.status !== "canceled" &&
      (!snapshot.cancelAt || snapshot.cancelAt > record.sourcePaidThrough))
  ) {
    throw new Error("Source can still renew; destination activation blocked");
  }
}

/** Verify money and ownership from Stripe read-back, not an invoice event's assertion. */
async function verifyPaidInvoice(
  client: Stripe,
  invoiceId: string,
  snapshot: VerifiedBillingSnapshot,
): Promise<string | undefined> {
  const invoice = await client.invoices.retrieve(invoiceId);
  if (
    stripeObjectId(invoice.customer) !== snapshot.customerId ||
    invoiceSubscriptionId(invoice) !== snapshot.subscriptionId
  ) {
    throw new Error("Paid invoice owner mismatch");
  }
  return invoice.status === "paid" &&
    invoice.amount_remaining === 0 &&
    snapshot.status === "active"
    ? invoice.id
    : undefined;
}

/** Resolve supported subscription-bearing events without trusting email or userId metadata. */
async function eventSubscription(
  event: Stripe.Event,
  client: Stripe,
): Promise<Stripe.Subscription | undefined> {
  if (event.type.startsWith("customer.subscription.")) {
    const id = stripeObjectId(event.data.object);
    return id ? client.subscriptions.retrieve(id) : undefined;
  }
  if (event.type.startsWith("invoice.")) {
    const id = invoiceSubscriptionId(event.data.object as Stripe.Invoice);
    return id ? client.subscriptions.retrieve(id) : undefined;
  }
  return undefined;
}

/** Own migration events completely, so old handlers cannot send email or overwrite a new binding. */
export async function handleMigrationEvent(
  accountId: string,
  event: Stripe.Event,
  dependencies: MigrationEngineDependencies,
): Promise<boolean> {
  const { isBillingRiskEvent, resolveBillingRisk } = await import("./stripe-billing-safety");
  const risk = isBillingRiskEvent(event) ? await resolveBillingRisk(event, await dependencies.client(accountId)) : null;
  const object = event.data.object as Stripe.Event.Data.Object & {
    customer?: unknown;
  };
  let customerId =
    event.type.startsWith("customer.") &&
    !event.type.startsWith("customer.subscription.")
      ? stripeObjectId(object)
      : stripeObjectId(object.customer);
  if (risk) customerId = risk.customerId;
  if (!customerId) return false;
  const record = await dependencies.store.byCustomer(accountId, customerId);
  if (!record) return false;
  parseBillingMigration(record);
  if (event.livemode !== record.livemode)
    throw new Error("Migration mode mismatch");
  // One-off usage purchases keep their independently verified Checkout path.
  if (
    event.type.startsWith("checkout.session.") &&
    "mode" in object &&
    object.mode === "payment"
  )
    return false;
  if (record.stage === "cancelled" && accountId === record.source.accountId)
    return false;
  if (
    record.stage === "destination_active" &&
    accountId === record.destination?.accountId &&
    event.type === "checkout.session.completed"
  )
    return false;
  if (isBillingRiskEvent(event)) {
    if (!risk || risk.charge.metadata.shiori_kind === "usage_topup") return false;
    const affected = accountId === record.source.accountId ? record.source : record.destination;
    if (!risk.subscriptionId || risk.subscriptionId !== affected?.subscriptionId) return true;
    await dependencies.store.commit(record, {
      kind: "review",
      reason: "Refund or dispute requires account-qualified review",
    });
    if (record.stage !== "destination_active")
      await cancelMigrationReplacement(record, dependencies);
    return true;
  }
  const client = await dependencies.client(accountId);
  const sub = await eventSubscription(event, client);
  if (!sub) return true;
  const snapshot = subscriptionSnapshot(accountId, sub);
  if (snapshot.customerId !== customerId)
    throw new Error("Subscription customer mismatch");
  const fromSource =
    accountId === record.source.accountId &&
    sub.id === record.source.subscriptionId;
  const destination = record.destination;
  const fromDestination =
    accountId === destination?.accountId &&
    customerId === destination.customerId &&
    (destination.subscriptionId === sub.id ||
      (!destination.subscriptionId &&
        Boolean(destination.scheduleId) &&
        destination.scheduleId === snapshot.scheduleId));
  if (!fromSource && !fromDestination) return true;
  if (fromSource) {
    const previous = event.data.previous_attributes as
      Partial<Stripe.Subscription> | undefined;
    const newlyCancelled =
      previous &&
      ((previous.cancel_at_period_end === false && sub.cancel_at_period_end) ||
        (previous.cancel_at === null && Boolean(sub.cancel_at)));
    if (
      snapshot.cancelAt &&
      newlyCancelled &&
      sub.metadata[MIGRATION_OPERATION_METADATA] !== record.operationId &&
      event.type === "customer.subscription.updated"
    ) {
      await requestMigrationCancellation(record, dependencies);
    } else if (
      record.stage !== "prepared" &&
      record.stage !== "rollback_verified" &&
      !snapshot.cancelAt &&
      sub.status !== "canceled"
    ) {
      await dependencies.store.commit(record, {
        kind: "review",
        reason: "Legacy renewal resumed during transfer",
      });
      await cancelMigrationReplacement(record, dependencies);
    } else if (
      record.stage === "prepared" ||
      record.stage === "rollback_verified"
    ) {
      if (
        snapshot.cancelAt &&
        sub.metadata[MIGRATION_OPERATION_METADATA] === record.operationId
      ) {
        await dependencies.store.commit(record, { kind: "refresh", snapshot });
      } else if (dependencies.dispatchCurrent) {
        await dependencies.dispatchCurrent(event, sub);
      } else {
        if (event.type.startsWith("customer.subscription."))
          event.data.object = sub;
        return false;
      }
    }
    return true;
  }
  if (record.stage === "destination_active") {
    if (dependencies.dispatchCurrent) {
      await dependencies.dispatchCurrent(event, sub);
      return true;
    }
    if (event.type.startsWith("customer.subscription."))
      event.data.object = sub;
    return false;
  }
  if (record.customerCancellationRequested || record.reviewReason) {
    await cancelMigrationReplacement(record, dependencies);
    return true;
  }
  // After handover, the ordinary upgrade/payment-retry policy owns this exact
  // subscription. It still runs behind account-qualified database guards.
  if (
    event.type === "invoice.paid" ||
    event.type === "invoice.payment_succeeded"
  ) {
    const invoiceId = stripeObjectId(event.data.object);
    if (!invoiceId) throw new Error("Invoice ID missing");
    snapshot.paidInvoiceId = await verifyPaidInvoice(
      client,
      invoiceId,
      snapshot,
    );
    if (snapshot.paidInvoiceId) {
      await assertSourceStopped(record, dependencies);
      if (snapshot.periodStart !== record.sourcePaidThrough)
        throw new Error(
          "Destination started before/after the original renewal",
        );
      await dependencies.store.commit(record, { kind: "activate", snapshot });
    }
  } else if (
    event.type === "invoice.payment_failed" ||
    event.type === "invoice.payment_action_required"
  ) {
    if (Date.now() < record.sourcePaidThrough * 1000)
      throw new Error(
        "Prepaid source access has not expired; retry reconciliation at renewal",
      );
    if (snapshot.periodStart !== record.sourcePaidThrough)
      throw new Error("Recovery invoice does not preserve the original renewal");
    await assertSourceStopped(record, dependencies);
    await dependencies.store.commit(record, {
      kind: "payment_failed",
      snapshot,
    });
  }
  return true;
}
