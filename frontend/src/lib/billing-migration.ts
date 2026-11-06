/** Pure migration policy. No Stripe writes, emails, or database mutations. */
export type MigrationStage =
  | "prepared"
  | "source_stopped"
  | "destination_staged"
  | "destination_active"
  | "rollback_verified";

export type BillingReference = {
  accountId: string;
  customerId: string;
  subscriptionId: string;
};

export type BillingMigration = {
  operationId: string;
  appUserId: string;
  source: BillingReference;
  destination?: BillingReference;
  stage: MigrationStage;
  sourcePaidThrough: number;
  sourceStoppedVerified: boolean;
  destinationPaidInvoiceVerified: boolean;
  sourceResumedVerified: boolean;
  destinationDisabledVerified: boolean;
  customerCancellationRequested: boolean;
  complimentary: boolean;
  livemode: boolean;
};

export type BillingRoute =
  | { kind: "account"; reference: BillingReference }
  | { kind: "hold"; reason: "transition" | "customer_cancellation" };

const stages: readonly MigrationStage[] = [
  "prepared", "source_stopped", "destination_staged", "destination_active", "rollback_verified",
];

/** Reject missing records before reading persisted migration fields. */
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("Migration record must be an object");
  }
  return value as Record<string, unknown>;
}

/** Read a required operator identity without coercion. */
function text(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) throw new TypeError("Missing migration identity");
  return value;
}

/** Parse a customer and subscription in their owning Stripe account. */
function reference(value: unknown): BillingReference {
  const row = record(value);
  const result = {
    accountId: text(row.accountId), customerId: text(row.customerId), subscriptionId: text(row.subscriptionId),
  };
  if (!/^acct_\w+$/.test(result.accountId) || !/^cus_\w+$/.test(result.customerId) || !/^sub_\w+$/.test(result.subscriptionId)) {
    throw new TypeError("Malformed account-qualified billing reference");
  }
  return result;
}

/** Proof flags must be explicit booleans, never truthy text. */
function boolean(value: unknown): boolean {
  if (typeof value !== "boolean") throw new TypeError("Missing migration proof flag");
  return value;
}

/** Validate an operator ledger before it can influence billing routing. */
export function parseBillingMigration(value: unknown): BillingMigration {
  const row = record(value);
  if (!stages.includes(row.stage as MigrationStage)) throw new TypeError("Unknown migration stage");
  if (typeof row.sourcePaidThrough !== "number" || !Number.isSafeInteger(row.sourcePaidThrough) || row.sourcePaidThrough <= 0 || row.sourcePaidThrough >= 100_000_000_000) {
    throw new TypeError("Paid-through time must be epoch seconds");
  }
  const migration: BillingMigration = {
    operationId: text(row.operationId), appUserId: text(row.appUserId), source: reference(row.source),
    ...(row.destination === undefined ? {} : { destination: reference(row.destination) }),
    stage: row.stage as MigrationStage, sourcePaidThrough: row.sourcePaidThrough,
    sourceStoppedVerified: boolean(row.sourceStoppedVerified),
    destinationPaidInvoiceVerified: boolean(row.destinationPaidInvoiceVerified),
    sourceResumedVerified: boolean(row.sourceResumedVerified),
    destinationDisabledVerified: boolean(row.destinationDisabledVerified),
    customerCancellationRequested: boolean(row.customerCancellationRequested),
    complimentary: boolean(row.complimentary), livemode: boolean(row.livemode),
  };
  if (migration.destination?.accountId === migration.source.accountId) throw new TypeError("Source and destination must differ");
  if (migration.stage === "destination_active" && (!migration.destination || !migration.sourceStoppedVerified || !migration.destinationPaidInvoiceVerified)) {
    throw new TypeError("Activation requires destination invoice and source-stop read-back proof");
  }
  if (migration.stage === "destination_staged" && (!migration.destination || !migration.sourceStoppedVerified)) {
    throw new TypeError("Destination staging requires source-stop read-back proof");
  }
  if (migration.stage === "source_stopped" && !migration.sourceStoppedVerified) {
    throw new TypeError("Source-stop state requires read-back proof");
  }
  if (migration.stage === "rollback_verified" && (!migration.sourceResumedVerified || !migration.destinationDisabledVerified)) {
    throw new TypeError("Rollback requires destination-stop and source-resume read-back proofs");
  }
  return migration;
}

/** Portal/upgrade/resume routing must never silently fall back to another account. */
export function resolveMigrationBillingRoute(migration: BillingMigration): BillingRoute {
  migration = parseBillingMigration(migration);
  if (migration.customerCancellationRequested) return { kind: "hold", reason: "customer_cancellation" };
  if (migration.stage === "prepared" || migration.stage === "rollback_verified") {
    return { kind: "account", reference: migration.source };
  }
  if (migration.stage === "destination_active" && migration.destination) {
    return { kind: "account", reference: migration.destination };
  }
  return { kind: "hold", reason: "transition" };
}

export type MigrationEvent = BillingReference & {
  eventId: string;
  livemode: boolean;
  kind: "invoice_paid" | "payment_failed" | "subscription_changed" | "subscription_deleted" | "refund_or_dispute";
  cancellationRequested: boolean;
  migrationOperationId?: string;
};

export type MigrationEventDecision =
  | "ignore_wrong_account_or_mode"
  | "ignore_superseded_subscription"
  | "preserve_prepaid_access"
  | "record_customer_cancel_and_stop_destination"
  | "reconcile_destination_activation"
  | "reconcile_transition_entitlement"
  | "reconcile_current_billing"
  | "review_refund_or_dispute";

/** Validate the account-qualified event adapter's output before using it. */
export function parseMigrationEvent(value: unknown): MigrationEvent {
  const row = record(value);
  const kinds: readonly MigrationEvent["kind"][] = [
    "invoice_paid", "payment_failed", "subscription_changed", "subscription_deleted", "refund_or_dispute",
  ];
  if (!kinds.includes(row.kind as MigrationEvent["kind"]) || !/^evt_\w+$/.test(text(row.eventId))) {
    throw new TypeError("Unknown migration event");
  }
  return {
    ...reference(row), eventId: text(row.eventId), livemode: boolean(row.livemode),
    kind: row.kind as MigrationEvent["kind"], cancellationRequested: boolean(row.cancellationRequested),
    ...(row.migrationOperationId === undefined ? {} : { migrationOperationId: text(row.migrationOperationId) }),
  };
}

/** IDs can only identify a billing owner together with the account. */
function matches(a: BillingReference, b: BillingReference): boolean {
  return a.accountId === b.accountId && a.customerId === b.customerId && a.subscriptionId === b.subscriptionId;
}

/** Decide ownership before calling existing app handlers or sending notifications. */
export function classifyMigrationEvent(
  migration: BillingMigration,
  event: MigrationEvent,
  nowSeconds: number = Math.floor(Date.now() / 1000),
): MigrationEventDecision {
  migration = parseBillingMigration(migration);
  event = parseMigrationEvent(event);
  if (!Number.isSafeInteger(nowSeconds) || nowSeconds <= 0) throw new TypeError("Invalid event evaluation time");
  const prepaidDecision = nowSeconds < migration.sourcePaidThrough
    ? "preserve_prepaid_access"
    : "reconcile_transition_entitlement";
  if (event.livemode !== migration.livemode) return "ignore_wrong_account_or_mode";
  const fromSource = matches(migration.source, event);
  const fromDestination = migration.destination ? matches(migration.destination, event) : false;
  if (!fromSource && !fromDestination) return "ignore_wrong_account_or_mode";
  if (event.kind === "refund_or_dispute") return "review_refund_or_dispute";
  if (fromSource && event.cancellationRequested && event.migrationOperationId !== migration.operationId) {
    return "record_customer_cancel_and_stop_destination";
  }
  if (fromSource && migration.stage === "destination_active") return "ignore_superseded_subscription";
  if (fromSource && (migration.stage === "source_stopped" || migration.stage === "destination_staged") &&
      (event.kind === "subscription_deleted" || (event.kind === "subscription_changed" && event.migrationOperationId === migration.operationId))) {
    return prepaidDecision;
  }
  if (fromDestination && migration.stage !== "destination_active") {
    return event.kind === "invoice_paid" ? "reconcile_destination_activation" : prepaidDecision;
  }
  return "reconcile_current_billing";
}

/** Dedupe ledgers must include account and mode, not just an event ID. */
export function migrationEventKey(event: MigrationEvent): string {
  event = parseMigrationEvent(event);
  return `${event.livemode ? "live" : "test"}:${event.accountId}:${event.eventId}`;
}
