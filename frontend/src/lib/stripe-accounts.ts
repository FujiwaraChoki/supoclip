import { AsyncLocalStorage } from "node:async_hooks";
import Stripe from "stripe";

export type StripeAccountContext = {
  accountId: string;
  client: Stripe;
  recoveryOnly?: boolean;
  customerId?: string;
};
const scope = new AsyncLocalStorage<StripeAccountContext>();
const clients = new Map<
  string,
  { key: string; client: Stripe; verified?: Promise<void> }
>();

/** Deployment-only gate; absent configuration preserves the legacy integration. */
export function stripeAccountsEnabled(): boolean {
  return process.env.STRIPE_ACCOUNT_MIGRATION_ENABLED === "true";
}

/** Read the current server request's account without inferring it from an ID. */
export function stripeAccountContext(): StripeAccountContext | undefined {
  return scope.getStore();
}

/** Reject unconfigured accounts before reading credentials or calling Stripe. */
export function stripeAccountRole(accountId: string): "source" | "destination" {
  if (
    process.env.STRIPE_SOURCE_ACCOUNT_ID ===
    process.env.STRIPE_DESTINATION_ACCOUNT_ID
  )
    throw new Error("Stripe source and destination must differ");
  if (!/^acct_[A-Za-z0-9]+$/.test(accountId))
    throw new Error("Invalid Stripe account");
  if (accountId === process.env.STRIPE_SOURCE_ACCOUNT_ID) return "source";
  if (accountId === process.env.STRIPE_DESTINATION_ACCOUNT_ID)
    return "destination";
  throw new Error("Stripe account is not configured");
}

/** Legacy data belongs to the explicitly configured source account. */
export function sourceStripeAccountId(): string {
  const id = process.env.STRIPE_SOURCE_ACCOUNT_ID;
  if (!id || stripeAccountRole(id) !== "source")
    throw new Error("Stripe source account is not configured");
  return id;
}

/** New checkout stays on the source unless a later release explicitly switches it. */
export function checkoutStripeAccountId(): string {
  const id = process.env.STRIPE_CHECKOUT_ACCOUNT_ID || sourceStripeAccountId();
  stripeAccountRole(id);
  return id;
}

/** Obtain an SDK client for signature verification; this performs no network I/O. */
export function configuredStripeClient(accountId: string): Stripe {
  const role = stripeAccountRole(accountId);
  const key =
    role === "source"
      ? process.env.STRIPE_SECRET_KEY
      : process.env.STRIPE_DESTINATION_SECRET_KEY;
  if (!key || !/^(?:sk|rk|rkcs)_(?:test|live)_/.test(key))
    throw new Error("Stripe account credentials are not configured");
  const cached = clients.get(accountId);
  if (cached?.key === key) return cached.client;
  const client = new Stripe(key);
  clients.set(accountId, { key, client });
  return client;
}

/** Never use a webhook secret from a different account, or guess from the payload. */
export function stripeWebhookSecret(accountId: string): string {
  const role = stripeAccountRole(accountId);
  const secret =
    role === "source"
      ? process.env.STRIPE_WEBHOOK_SECRET
      : process.env.STRIPE_DESTINATION_WEBHOOK_SECRET;
  if (!secret) throw new Error("Stripe signing secret is not configured");
  return secret;
}

/** Check mode locally before any event can reach a database writer. */
export function assertStripeEventMode(
  accountId: string,
  event: Stripe.Event,
): void {
  const role = stripeAccountRole(accountId);
  const key =
    role === "source"
      ? process.env.STRIPE_SECRET_KEY
      : process.env.STRIPE_DESTINATION_SECRET_KEY;
  const live = Boolean(key?.includes("_live_"));
  if (
    event.livemode !== live ||
    (event.account && event.account !== accountId)
  ) {
    throw new Error("Stripe event account or mode mismatch");
  }
}

/** Bind credentials to their actual account once; a failed identity check is retryable. */
export async function verifiedStripeClient(accountId: string): Promise<Stripe> {
  const client = configuredStripeClient(accountId);
  const cached = clients.get(accountId);
  if (!cached) throw new Error("Stripe client cache unavailable");
  if (!cached.verified) {
    cached.verified = client.accounts
      .retrieve()
      .then((account) => {
        if (account.id !== accountId)
          throw new Error("Stripe credential/account mismatch");
      })
      .catch(() => {
        cached.verified = undefined;
        throw new Error("Unable to verify Stripe account identity");
      });
  }
  await cached.verified;
  return client;
}

/** Isolate concurrent requests and keep their SDKs/price aliases together. */
export function runWithStripeAccount<T>(
  context: StripeAccountContext,
  callback: () => T,
): T {
  return scope.run(context, callback);
}

/** Missing request context fails closed when multi-account routing is enabled. */
export function scopedStripeClient(legacy: () => Stripe): Stripe {
  const current = scope.getStore();
  if (current) return current.client;
  if (stripeAccountsEnabled())
    throw new Error("Stripe operation requires an account context");
  return legacy();
}

/** Scope internal invoices and aliases to an account, without exposing keys to callers. */
export function stripePriceEnvPrefix(): string {
  const current = scope.getStore();
  return current && stripeAccountRole(current.accountId) === "destination"
    ? "STRIPE_DESTINATION_"
    : "STRIPE_";
}

/** Retain ordinary error handling while distinguishing a safe transition hold. */
export class BillingAccountHoldError extends Error {
  constructor() {
    super("Your billing transfer is being verified. Please try again shortly.");
    this.name = "BillingAccountHoldError";
  }
}

/** Separate branded portal configurations are required in the shared US account. */
export function accountPortalConfiguration(): { configuration?: string } {
  const prefix = stripePriceEnvPrefix();
  const configuration =
    process.env[`${prefix}CUSTOMER_PORTAL_CONFIGURATION_ID`];
  if (prefix === "STRIPE_DESTINATION_" && !configuration)
    throw new BillingAccountHoldError();
  return configuration ? { configuration } : {};
}
