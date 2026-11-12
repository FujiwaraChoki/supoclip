import { describe, expect, test } from "vitest";
import {
  classifyMigrationEvent as classifyMigrationEventAtTime, migrationEventKey, parseBillingMigration, resolveMigrationBillingRoute,
  type BillingMigration, type MigrationEvent,
} from "./billing-migration";

const source = { accountId: "acct_source", customerId: "cus_old", subscriptionId: "sub_old" };
const destination = { accountId: "acct_destination", customerId: "cus_new", subscriptionId: "sub_new" };
const base: BillingMigration = {
  operationId: "migration_123", appUserId: "user_123", source, destination, stage: "prepared", sourcePaidThrough: 1800000000,
  sourceStoppedVerified: false, destinationPaidInvoiceVerified: false, sourceResumedVerified: false, destinationDisabledVerified: false,
  customerCancellationRequested: false, complimentary: false, livemode: true,
};
const active: BillingMigration = { ...base, stage: "destination_active", sourceStoppedVerified: true, destinationPaidInvoiceVerified: true };
const event: MigrationEvent = { ...source, eventId: "evt_123", livemode: true, kind: "subscription_changed", cancellationRequested: false };
/** Keep clock-sensitive policy tests independent of the wall clock. */
const classifyMigrationEvent = (migration: BillingMigration, incoming: MigrationEvent, now = 1_790_000_000) =>
  classifyMigrationEventAtTime(migration, incoming, now);

describe("account migration policy", () => {
  test("uses the source until a migration is activated", () => {
    expect(resolveMigrationBillingRoute(parseBillingMigration(base))).toEqual({ kind: "account", reference: source });
    expect(resolveMigrationBillingRoute({ ...base, stage: "rollback_verified", sourceResumedVerified: true, destinationDisabledVerified: true })).toEqual({ kind: "account", reference: source });
  });
  test("does not resume or upgrade a source subscription while the destination is staged", () => {
    expect(resolveMigrationBillingRoute({ ...base, stage: "destination_staged", sourceStoppedVerified: true })).toEqual({ kind: "hold", reason: "transition" });
  });
  test("requires paid invoice and source-stop proofs before routing to destination", () => {
    expect(() => parseBillingMigration({ ...base, stage: "destination_active" })).toThrow();
    expect(() => parseBillingMigration({ ...base, stage: "destination_active", sourceStoppedVerified: true })).toThrow();
    const active = parseBillingMigration({ ...base, stage: "destination_active", sourceStoppedVerified: true, destinationPaidInvoiceVerified: true });
    expect(resolveMigrationBillingRoute(active)).toEqual({ kind: "account", reference: destination });
  });
  test("preserves prepaid access when an old migration-owned cancellation arrives", () => {
    const staged: BillingMigration = { ...base, stage: "destination_staged", sourceStoppedVerified: true };
    expect(classifyMigrationEvent(staged, { ...event, cancellationRequested: true, migrationOperationId: base.operationId })).toBe("preserve_prepaid_access");
    expect(classifyMigrationEvent(staged, { ...event, kind: "subscription_deleted" })).toBe("preserve_prepaid_access");
  });
  test("carries a real customer cancellation into the destination instead of renewing them", () => {
    expect(classifyMigrationEvent(base, { ...event, cancellationRequested: true })).toBe("record_customer_cancel_and_stop_destination");
    expect(classifyMigrationEvent(active, { ...event, cancellationRequested: true })).toBe("record_customer_cancel_and_stop_destination");
    expect(resolveMigrationBillingRoute({ ...base, customerCancellationRequested: true })).toEqual({ kind: "hold", reason: "customer_cancellation" });
  });
  test("ignores late old events after the new subscription takes over", () => {
    for (const kind of ["subscription_deleted", "subscription_changed", "invoice_paid", "payment_failed"] as const) {
      expect(classifyMigrationEvent(active, { ...event, kind })).toBe("ignore_superseded_subscription");
    }
  });
  test("does not grant a future destination subscription before its paid invoice", () => {
    expect(classifyMigrationEvent(base, { ...event, ...destination })).toBe("preserve_prepaid_access");
    expect(classifyMigrationEvent(base, { ...event, ...destination, kind: "invoice_paid" })).toBe("reconcile_destination_activation");
  });
  test("does not extend prepaid access forever when the destination payment fails", () => {
    expect(classifyMigrationEvent(base, { ...event, ...destination, kind: "payment_failed" }, base.sourcePaidThrough + 1)).toBe("reconcile_transition_entitlement");
    expect(() => classifyMigrationEvent(base, event, NaN)).toThrow();
  });
  test("does not mix accounts, customers, subscriptions, or test mode", () => {
    for (const change of [{ accountId: "acct_wrong" }, { customerId: "cus_wrong" }, { subscriptionId: "sub_wrong" }, { livemode: false }]) {
      expect(classifyMigrationEvent(base, { ...event, ...change })).toBe("ignore_wrong_account_or_mode");
    }
  });
  test("does not silently discard legacy refunds or disputes", () => {
    expect(classifyMigrationEvent(active, { ...event, kind: "refund_or_dispute" })).toBe("review_refund_or_dispute");
  });
  test("scopes duplicate delivery keys by account and mode", () => {
    expect(migrationEventKey(event)).not.toBe(migrationEventKey({ ...event, accountId: "acct_destination" }));
    expect(migrationEventKey(event)).not.toBe(migrationEventKey({ ...event, livemode: false }));
    expect(migrationEventKey(event)).toBe(migrationEventKey({ ...event }));
  });
  test("rejects corrupt or incomplete stored ledgers", () => {
    for (const value of [null, [], {}, { ...base, stage: "unknown" }, { ...base, sourcePaidThrough: NaN },
      { ...base, sourceStoppedVerified: "yes" }, { ...base, sourcePaidThrough: 1800000000000 }, { ...base, stage: "source_stopped" }, { ...base, source: { ...source, accountId: "bad" } },
      { ...base, destination: { ...destination, accountId: source.accountId } }, { ...base, stage: "destination_staged" },
      { ...base, stage: "rollback_verified", sourceResumedVerified: true }]) {
      expect(() => parseBillingMigration(value)).toThrow();
    }
  });
  test("rejects an invalid ledger or event even when a caller bypasses the parser", () => {
    expect(() => resolveMigrationBillingRoute({ ...base, stage: "destination_active" })).toThrow();
    expect(() => classifyMigrationEvent(base, { ...event, eventId: "not_an_event" })).toThrow();
    expect(() => classifyMigrationEvent(base, { ...event, kind: "unknown" as MigrationEvent["kind"] })).toThrow();
  });
});
