import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import Stripe from "stripe";
import { runWithStripeAccount } from "../src/lib/stripe-accounts";
import {
  destinationCheckoutTax,
  ownedInvoiceRecoveryUrl,
  resolveBillingRisk,
  requireDestinationTaxLocation,
} from "../src/lib/stripe-billing-safety";

const sdk = new Stripe("sk_test_synthetic", {
  httpClient: Stripe.createFetchHttpClient(async () => {
    throw new Error("External network forbidden");
  }),
});
const scope = {
  accountId: "acct_destination",
  customerId: "cus_owned",
  client: sdk,
};
const env = [
  "STRIPE_SOURCE_ACCOUNT_ID",
  "STRIPE_DESTINATION_ACCOUNT_ID",
  "STRIPE_DESTINATION_REQUIRED_TAX_REGISTRATION_IDS",
];
const saved = new Map(env.map((key) => [key, process.env[key]]));
const settings = vi.spyOn(sdk.tax.settings, "retrieve");
const registration = vi.spyOn(sdk.tax.registrations, "retrieve");
const subscriptions = vi.spyOn(sdk.subscriptions, "retrieve");
const invoices = vi.spyOn(sdk.invoices, "retrieve");
const charges = vi.spyOn(sdk.charges, "retrieve");
const payments = vi.spyOn(sdk.invoicePayments, "list");
const customers = vi.spyOn(sdk.customers, "retrieve");
const updateCustomer = vi.spyOn(sdk.customers, "update");

// Fixtures intentionally contain only the SDK response fields consumed at this boundary.
beforeEach(() => {
  process.env.STRIPE_SOURCE_ACCOUNT_ID = "acct_source";
  process.env.STRIPE_DESTINATION_ACCOUNT_ID = scope.accountId;
  process.env.STRIPE_DESTINATION_REQUIRED_TAX_REGISTRATION_IDS =
    "taxreg_confirmed";
  settings.mockResolvedValue({
    status: "active",
    defaults: { tax_behavior: "exclusive" },
  } as Stripe.Response<Stripe.Tax.Settings>);
  registration.mockResolvedValue({
    id: "taxreg_confirmed",
    country: "IE",
    country_options: { ie: { type: "oss_non_union" } },
    status: "active",
    active_from: 1,
  } as Stripe.Response<Stripe.Tax.Registration>);
  subscriptions.mockResolvedValue({
    id: "sub_owned",
    customer: "cus_owned",
    latest_invoice: "in_owned",
  } as Stripe.Response<Stripe.Subscription>);
  invoices.mockResolvedValue({
    id: "in_owned",
    customer: "cus_owned",
    parent: { subscription_details: { subscription: "sub_owned" } },
    status: "open",
    amount_remaining: 1200,
    hosted_invoice_url: "https://invoice.stripe.com/i/owned",
  } as Stripe.Response<Stripe.Invoice>);
  charges.mockResolvedValue({
    id: "ch_owned",
    customer: "cus_owned",
    refunded: true,
    payment_intent: "pi_owned",
    metadata: {},
  } as Stripe.Response<Stripe.Charge>);
  payments.mockResolvedValue({
    data: [{ invoice: "in_owned" }],
    has_more: false,
  } as Stripe.Response<Stripe.ApiList<Stripe.InvoicePayment>>);
  customers.mockResolvedValue({
    id: "cus_owned",
    tax: { location: { country: "DE" }, automatic_tax: "supported" },
  } as Stripe.Response<Stripe.Customer>);
  updateCustomer.mockResolvedValue({
    id: "cus_owned",
  } as Stripe.Response<Stripe.Customer>);
  for (const spy of [
    settings,
    registration,
    subscriptions,
    invoices,
    charges,
    payments,
    customers,
    updateCustomer,
  ])
    spy.mockClear();
});
afterEach(() => {
  for (const key of env) {
    const value = saved.get(key);
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
});

describe("destination tax preflight and location collection", () => {
  test("source checkout does not acquire destination settings or change legacy policy", async () => {
    expect(
      await runWithStripeAccount(
        { ...scope, accountId: "acct_source" },
        destinationCheckoutTax,
      ),
    ).toEqual({});
    expect(settings).not.toHaveBeenCalled();
  });
  test("confirmed exclusive policy collects full address and business tax IDs", async () => {
    expect(await runWithStripeAccount(scope, destinationCheckoutTax)).toEqual({
      automatic_tax: { enabled: true },
      billing_address_collection: "required",
      customer_update: { address: "auto", name: "auto" },
      tax_id_collection: { enabled: true },
    });
    expect(registration).toHaveBeenCalledWith("taxreg_confirmed");
  });
  test("no registration confirmation holds instead of silently calculating zero tax", async () => {
    delete process.env.STRIPE_DESTINATION_REQUIRED_TAX_REGISTRATION_IDS;
    await expect(
      runWithStripeAccount(scope, destinationCheckoutTax),
    ).rejects.toThrow("verified");
    expect(settings).not.toHaveBeenCalled();
  });
  test("pending head office or inclusive default cannot proceed", async () => {
    for (const value of [
      { status: "pending", defaults: { tax_behavior: "exclusive" } },
      { status: "active", defaults: { tax_behavior: "inclusive" } },
    ]) {
      settings.mockResolvedValue(value as Stripe.Response<Stripe.Tax.Settings>);
      await expect(
        runWithStripeAccount(scope, destinationCheckoutTax),
      ).rejects.toThrow();
    }
  });
  test("scheduled/expired/future registrations cannot substitute for active collection", async () => {
    for (const value of [
      { status: "scheduled", active_from: 1 },
      { status: "expired", active_from: 1 },
      { status: "active", active_from: Date.now() / 1000 + 10000 },
    ]) {
      registration.mockResolvedValue(
        value as Stripe.Response<Stripe.Tax.Registration>,
      );
      await expect(
        runWithStripeAccount(scope, destinationCheckoutTax),
      ).rejects.toThrow();
    }
  });
  test("tax API outages propagate rather than using a cached-ready flag", async () => {
    registration.mockRejectedValue(new Error("Tax unavailable"));
    await expect(
      runWithStripeAccount(scope, destinationCheckoutTax),
    ).rejects.toThrow("unavailable");
  });
  test("native validated locations outside collecting jurisdictions remain valid", async () => {
    customers.mockResolvedValue({
      id: "cus_owned",
      tax: { location: { country: "CH" }, automatic_tax: "not_collecting" },
    } as Stripe.Response<Stripe.Customer>);
    await runWithStripeAccount(scope, () =>
      requireDestinationTaxLocation("cus_owned"),
    );
  });
  test("native address is validated by Stripe, not inferred from the owner's address", async () => {
    const address = {
      line1: "Synthetic 1",
      city: "Berlin",
      country: "DE",
      postal_code: "10115",
    };
    await runWithStripeAccount(scope, () =>
      requireDestinationTaxLocation("cus_owned", address),
    );
    expect(updateCustomer).toHaveBeenCalledWith("cus_owned", {
      address,
      tax: { validate_location: "immediately" },
    });
    customers.mockResolvedValue({
      id: "cus_owned",
      tax: { location: null, automatic_tax: "unrecognized_location" },
    } as Stripe.Response<Stripe.Customer>);
    await expect(
      runWithStripeAccount(scope, () =>
        requireDestinationTaxLocation("cus_owned"),
      ),
    ).rejects.toThrow("billing address");
  });
});

describe("authenticated invoice challenge recovery", () => {
  test("uses the owned current unpaid invoice rather than creating a card-update session", async () => {
    expect(
      await runWithStripeAccount(scope, () =>
        ownedInvoiceRecoveryUrl("sub_owned"),
      ),
    ).toBe("https://invoice.stripe.com/i/owned");
    expect(subscriptions).toHaveBeenCalledWith("sub_owned");
  });
  test("wrong customer and wrong subscription references cannot disclose invoice URLs", async () => {
    subscriptions.mockResolvedValue({
      customer: "cus_someone_else",
    } as Stripe.Response<Stripe.Subscription>);
    await expect(
      runWithStripeAccount(scope, () => ownedInvoiceRecoveryUrl("sub_owned")),
    ).rejects.toThrow();
    expect(invoices).not.toHaveBeenCalled();
    subscriptions.mockResolvedValue({
      customer: "cus_owned",
      latest_invoice: "in_other",
    } as Stripe.Response<Stripe.Subscription>);
    invoices.mockResolvedValue({
      customer: "cus_owned",
      parent: { subscription_details: { subscription: "sub_other" } },
    } as Stripe.Response<Stripe.Invoice>);
    await expect(
      runWithStripeAccount(scope, () => ownedInvoiceRecoveryUrl("sub_owned")),
    ).rejects.toThrow();
  });
  test("already-paid invoices wait for reconciliation rather than asking for another payment", async () => {
    invoices.mockResolvedValue({
      customer: "cus_owned",
      parent: { subscription_details: { subscription: "sub_owned" } },
      status: "paid",
      amount_remaining: 0,
    } as Stripe.Response<Stripe.Invoice>);
    expect(
      await runWithStripeAccount(scope, () =>
        ownedInvoiceRecoveryUrl("sub_owned"),
      ),
    ).toBeNull();
  });
});

describe("charge graph risk ownership", () => {
  const event = (type: string) =>
    ({
      type,
      data: {
        object:
          type === "charge.refunded"
            ? { id: "ch_owned" }
            : { id: "dp_owned", charge: "ch_owned" },
      },
    }) as Stripe.Event;
  test("refunds, disputes and fraud warnings resolve modern invoice payment references", async () => {
    for (const type of [
      "charge.refunded",
      "charge.dispute.created",
      "radar.early_fraud_warning.created",
    ]) {
      expect(await resolveBillingRisk(event(type), sdk)).toMatchObject({
        customerId: "cus_owned",
        subscriptionId: "sub_owned",
      });
    }
    expect(payments).toHaveBeenCalledWith({
      payment: { type: "payment_intent", payment_intent: "pi_owned" },
      limit: 2,
    });
  });
  test("partial refunds and unrelated charge events do not revoke paid subscriptions", async () => {
    charges.mockResolvedValue({
      id: "ch_owned",
      refunded: false,
    } as Stripe.Response<Stripe.Charge>);
    expect(await resolveBillingRisk(event("charge.refunded"), sdk)).toBeNull();
    expect(await resolveBillingRisk(event("charge.succeeded"), sdk)).toBeNull();
    expect(payments).not.toHaveBeenCalled();
  });
  test("ambiguous invoice mappings and mismatched invoice customers must retry", async () => {
    payments.mockResolvedValue({
      data: [{ invoice: "in_1" }, { invoice: "in_2" }],
      has_more: false,
    } as Stripe.Response<Stripe.ApiList<Stripe.InvoicePayment>>);
    await expect(
      resolveBillingRisk(event("charge.refunded"), sdk),
    ).rejects.toThrow("Ambiguous");
    payments.mockResolvedValue({
      data: [{ invoice: "in_1" }],
      has_more: false,
    } as Stripe.Response<Stripe.ApiList<Stripe.InvoicePayment>>);
    invoices.mockResolvedValue({
      customer: "cus_other",
    } as Stripe.Response<Stripe.Invoice>);
    await expect(
      resolveBillingRisk(event("charge.refunded"), sdk),
    ).rejects.toThrow("mismatch");
  });
  test("one-off top-ups never resolve to the customer's ongoing subscription", async () => {
    const topupCharge = await sdk.charges.retrieve("ch_owned");
    charges.mockResolvedValue({ ...topupCharge, metadata: { shiori_kind: "usage_topup" } });
    expect(
      await resolveBillingRisk(event("charge.refunded"), sdk),
    ).toMatchObject({ customerId: "cus_owned" });
    expect(payments).not.toHaveBeenCalled();
  });
});

test("an unrelated active VAT registration cannot stand in for Irish Non-Union OSS", async () => {
  registration.mockResolvedValue({
    id: "taxreg_confirmed",
    country: "IE",
    country_options: { ie: { type: "standard" } },
    status: "active",
    active_from: 1,
  } as Stripe.Response<Stripe.Tax.Registration>);
  await expect(
    runWithStripeAccount(scope, destinationCheckoutTax),
  ).rejects.toThrow();
});

test("a canceled subscription cannot send the customer to pay an old open invoice", async () => {
  subscriptions.mockResolvedValue({ id: "sub_owned", customer: "cus_owned", status: "canceled", latest_invoice: "in_owned" } as Stripe.Response<Stripe.Subscription>);
  expect(await runWithStripeAccount(scope, () => ownedInvoiceRecoveryUrl("sub_owned"))).toBeNull();
  expect(invoices).not.toHaveBeenCalled();
});
