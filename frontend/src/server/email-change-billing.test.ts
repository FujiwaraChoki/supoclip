// @vitest-environment node
import Stripe from "stripe";
import { afterEach, describe, expect, it, vi } from "vitest";

import { billingCustomerRefs, updateBillingEmail, type BillingCustomerRef } from "./email-change-billing";

const SOURCE = "acct_source1";
const DESTINATION = "acct_destination1";

afterEach(() => vi.unstubAllEnvs());

function fakeDb(user: { stripe_customer_id: string | null; stripe_account_id: string | null } | null, migration: object | null = null) {
  return {
    user: { findUnique: vi.fn().mockResolvedValue(user) },
    stripeAccountMigration: { findUnique: vi.fn().mockResolvedValue(migration) },
  } as never;
}

describe("billingCustomerRefs", () => {
  it("uses the single legacy customer when account routing is off", async () => {
    const db = fakeDb({ stripe_customer_id: "cus_legacy", stripe_account_id: null });
    expect(await billingCustomerRefs(db, "u1")).toEqual([{ accountId: null, customerId: "cus_legacy" }]);
  });

  it("has nothing to update for users who never paid", async () => {
    expect(await billingCustomerRefs(fakeDb({ stripe_customer_id: null, stripe_account_id: null }), "u1")).toEqual([]);
  });

  it("covers both accounts during a migration, without duplicates", async () => {
    vi.stubEnv("STRIPE_ACCOUNT_MIGRATION_ENABLED", "true");
    vi.stubEnv("STRIPE_SOURCE_ACCOUNT_ID", SOURCE);
    vi.stubEnv("STRIPE_DESTINATION_ACCOUNT_ID", DESTINATION);
    const db = fakeDb(
      { stripe_customer_id: "cus_dest", stripe_account_id: DESTINATION },
      { source_account: SOURCE, source_customer: "cus_src", destination_account: DESTINATION, destination_customer: "cus_dest" },
    );
    expect(await billingCustomerRefs(db, "u1")).toEqual([
      { accountId: DESTINATION, customerId: "cus_dest" },
      { accountId: SOURCE, customerId: "cus_src" },
    ]);
  });

  it("puts customers without a recorded account on the legacy source account", async () => {
    vi.stubEnv("STRIPE_ACCOUNT_MIGRATION_ENABLED", "true");
    vi.stubEnv("STRIPE_SOURCE_ACCOUNT_ID", SOURCE);
    vi.stubEnv("STRIPE_DESTINATION_ACCOUNT_ID", DESTINATION);
    const db = fakeDb({ stripe_customer_id: "cus_old", stripe_account_id: null });
    expect(await billingCustomerRefs(db, "u1")).toEqual([{ accountId: SOURCE, customerId: "cus_old" }]);
  });
});

describe("updateBillingEmail", () => {
  const refs: BillingCustomerRef[] = [
    { accountId: SOURCE, customerId: "cus_src" },
    { accountId: DESTINATION, customerId: "cus_dest" },
  ];

  function clients(
    failOn?: { account: string; email: string; error?: Error },
    stored: Record<string, Partial<Stripe.Customer> | Stripe.DeletedCustomer> = {},
  ) {
    const calls: [string | null, string, string][] = [];
    const client = (accountId: string | null) => ({
      customers: {
        retrieve: vi.fn(async (customerId: string) => stored[customerId] ?? { id: customerId, email: "old@example.com" }),
        update: vi.fn(async (customerId: string, params: { email: string }) => {
          calls.push([accountId, customerId, params.email]);
          if (failOn && failOn.account === accountId && failOn.email === params.email) {
            throw failOn.error ?? new Error("Stripe is unavailable");
          }
          return {} as Stripe.Customer;
        }),
      },
    });
    return { calls, deps: { refs: async () => refs, client: async (id: string | null) => client(id) as never } };
  }

  it("moves every customer to the new address and can undo it", async () => {
    const { calls, deps } = clients();
    const undo = await updateBillingEmail(deps, "u1", "new@example.com");
    expect(calls).toEqual([
      [SOURCE, "cus_src", "new@example.com"],
      [DESTINATION, "cus_dest", "new@example.com"],
    ]);
    await undo();
    expect(calls.slice(2)).toEqual([
      [SOURCE, "cus_src", "old@example.com"],
      [DESTINATION, "cus_dest", "old@example.com"],
    ]);
  });

  it("gives each customer back the address Stripe had, not the sign-in address", async () => {
    const { calls, deps } = clients(undefined, {
      cus_src: { id: "cus_src", email: "billing@company.example" },
      cus_dest: { id: "cus_dest", email: null },
    });
    const undo = await updateBillingEmail(deps, "u1", "new@example.com");
    await undo();
    expect(calls.slice(2)).toEqual([
      [SOURCE, "cus_src", "billing@company.example"],
      [DESTINATION, "cus_dest", ""],
    ]);
  });

  it("restores the first account when the second fails", async () => {
    const { calls, deps } = clients({ account: DESTINATION, email: "new@example.com" });
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    await expect(updateBillingEmail(deps, "u1", "new@example.com")).rejects.toThrow("Stripe is unavailable");
    expect(calls).toEqual([
      [SOURCE, "cus_src", "new@example.com"],
      [DESTINATION, "cus_dest", "new@example.com"],
      [SOURCE, "cus_src", "old@example.com"],
    ]);
  });

  it("skips a customer Stripe no longer has", async () => {
    const missing = new Stripe.errors.StripeInvalidRequestError({ type: "invalid_request_error", code: "resource_missing", message: "No such customer" });
    const { calls, deps } = clients({ account: SOURCE, email: "new@example.com", error: missing });
    const undo = await updateBillingEmail(deps, "u1", "new@example.com");
    await undo();
    expect(calls).toEqual([
      [SOURCE, "cus_src", "new@example.com"],
      [DESTINATION, "cus_dest", "new@example.com"],
      [DESTINATION, "cus_dest", "old@example.com"],
    ]);
  });

  it("leaves a deleted customer alone", async () => {
    const { calls, deps } = clients(undefined, { cus_src: { id: "cus_src", object: "customer", deleted: true } });
    await updateBillingEmail(deps, "u1", "new@example.com");
    expect(calls).toEqual([[DESTINATION, "cus_dest", "new@example.com"]]);
  });
});
