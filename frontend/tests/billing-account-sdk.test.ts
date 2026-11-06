import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
const spyOn = vi.spyOn;
import Stripe from "stripe";
import {
  assertStripeEventMode,
  configuredStripeClient,
  runWithStripeAccount,
  scopedStripeClient,
  sourceStripeAccountId,
  stripeAccountContext,
  stripeWebhookSecret,
  verifiedStripeClient,
} from "../src/lib/stripe-accounts";
const accountEnvKeys = ["STRIPE_ACCOUNT_MIGRATION_ENABLED", "STRIPE_SOURCE_ACCOUNT_ID", "STRIPE_DESTINATION_ACCOUNT_ID", "STRIPE_SECRET_KEY", "STRIPE_DESTINATION_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_DESTINATION_WEBHOOK_SECRET"];
const originalAccountEnv = new Map(accountEnvKeys.map(key => [key, process.env[key]]));
afterEach(() => { for (const key of accountEnvKeys) { const value = originalAccountEnv.get(key); if (value === undefined) delete process.env[key]; else process.env[key] = value; } });
const source = "acct_sourcesdk";
const destination = "acct_destinationsdk";
beforeEach(() => {
  process.env.STRIPE_ACCOUNT_MIGRATION_ENABLED = "true";
  process.env.STRIPE_SOURCE_ACCOUNT_ID = source;
  process.env.STRIPE_DESTINATION_ACCOUNT_ID = destination;
  process.env.STRIPE_SECRET_KEY = "sk_test_source_fixture";
  process.env.STRIPE_DESTINATION_SECRET_KEY = "sk_test_destination_fixture";
  process.env.STRIPE_WEBHOOK_SECRET = "whsec_source";
  process.env.STRIPE_DESTINATION_WEBHOOK_SECRET = "whsec_destination";
});
describe("account-bound SDK configuration", () => {
  test("missing context/configuration cannot silently select the legacy client", () => {
    const legacy = configuredStripeClient(source);
    expect(() => scopedStripeClient(() => legacy)).toThrow("context");
    expect(() => configuredStripeClient("acct_unconfigured")).toThrow(
      "not configured",
    );
    delete process.env.STRIPE_DESTINATION_SECRET_KEY;
    expect(() => configuredStripeClient(destination)).toThrow("not configured");
    delete process.env.STRIPE_SOURCE_ACCOUNT_ID;
    expect(() => sourceStripeAccountId()).toThrow("not configured");
  });
  test("each secret and event mode stays bound to its configured account", () => {
    expect(stripeWebhookSecret(source)).toBe("whsec_source");
    expect(stripeWebhookSecret(destination)).toBe("whsec_destination");
    expect(() =>
      assertStripeEventMode(source, { livemode: true } as Stripe.Event),
    ).toThrow("mismatch");
    expect(() =>
      assertStripeEventMode(source, {
        livemode: false,
        account: destination,
      } as Stripe.Event),
    ).toThrow("mismatch");
    assertStripeEventMode(destination, { livemode: false } as Stripe.Event);
  });
  test("credential identity is checked and a failed check can retry", async () => {
    const sdk = configuredStripeClient(destination);
    const identity = spyOn(sdk.accounts, "retrieve").mockResolvedValue({
      id: source,
    } as Stripe.Response<Stripe.Account>);
    try {
      await expect(verifiedStripeClient(destination)).rejects.toThrow("verify");
      identity.mockResolvedValue({
        id: destination,
      } as Stripe.Response<Stripe.Account>);
      expect(await verifiedStripeClient(destination)).toBe(sdk);
    } finally {
      identity.mockRestore();
    }
  });
  test("concurrent requests cannot borrow another account's SDK or customer", async () => {
    const contexts = await Promise.all(
      [source, destination].map((accountId) =>
        runWithStripeAccount(
          {
            accountId,
            client: configuredStripeClient(accountId),
            customerId: "cus_unchanged",
          },
          async () => {
            await Promise.resolve();
            expect(
              scopedStripeClient(() => {
                throw Error("No fallback");
              }),
            ).toBe(configuredStripeClient(accountId));
            return stripeAccountContext()?.accountId;
          },
        ),
      ),
    );
    expect(contexts).toEqual([source, destination]);
    expect(stripeAccountContext()).toBeUndefined();
  });
});
