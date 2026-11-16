import type Stripe from "stripe";
import {
  stripeAccountContext,
  stripeAccountRole,
  BillingAccountHoldError,
} from "./stripe-accounts";
import {
  invoiceSubscriptionId,
  stripeObjectId,
} from "./stripe-migration-engine";

/** Require confirmed registrations in this account before destination sales. */
export async function destinationAutomaticTax(): Promise<{
  automatic_tax?: { enabled: true };
}> {
  const context = stripeAccountContext();
  if (!context || stripeAccountRole(context.accountId) !== "destination")
    return {};
  const ids = (
    process.env.STRIPE_DESTINATION_REQUIRED_TAX_REGISTRATION_IDS || ""
  )
    .split(",")
    .map((id) => id.trim())
    .filter(Boolean);
  if (
    !ids.length ||
    ids.length > 10 ||
    ids.some((id) => !/^taxreg_[A-Za-z0-9]+$/.test(id))
  )
    throw new BillingAccountHoldError();
  const settings = await context.client.tax.settings.retrieve();
  if (
    settings.status !== "active" ||
    settings.defaults.tax_behavior !== "exclusive"
  )
    throw new BillingAccountHoldError();
  let confirmedIrishOss = false;
  for (const id of new Set(ids)) {
    const registration = await context.client.tax.registrations.retrieve(id);
    if (
      registration.id !== id ||
      registration.status !== "active" ||
      registration.active_from > Math.floor(Date.now() / 1000)
    )
      throw new BillingAccountHoldError();
    if (
      registration.country === "IE" &&
      registration.country_options.ie?.type === "oss_non_union"
    )
      confirmedIrishOss = true;
  }
  if (!confirmedIrishOss) throw new BillingAccountHoldError();
  return { automatic_tax: { enabled: true } };
}

/** Hosted Checkout collects the location and business tax IDs rather than guessing them. */
export async function destinationCheckoutTax(): Promise<
  Partial<Stripe.Checkout.SessionCreateParams>
> {
  const tax = await destinationAutomaticTax();
  return tax.automatic_tax
    ? {
        ...tax,
        billing_address_collection: "required",
        customer_update: { address: "auto", name: "auto" },
        tax_id_collection: { enabled: true },
      }
    : {};
}

export class BillingAddressRequiredError extends Error {
  /** The native client must collect an address or use hosted Checkout. */
  constructor() {
    super(
      "Please provide your billing address or subscribe through the website.",
    );
    this.name = "BillingAddressRequiredError";
  }
}

/** Native checkout validates location before invoice creation; invalid data never charges. */
export async function requireDestinationTaxLocation(
  customerId: string,
  address?: Stripe.AddressParam,
): Promise<void> {
  const context = stripeAccountContext();
  if (!context || stripeAccountRole(context.accountId) !== "destination")
    return;
  if (address)
    await context.client.customers.update(customerId, {
      address,
      tax: { validate_location: "immediately" },
    });
  const customer = await context.client.customers.retrieve(customerId, {
    expand: ["tax"],
  });
  if (
    customer.deleted ||
    !customer.tax?.location ||
    !["supported", "not_collecting"].includes(customer.tax.automatic_tax)
  )
    throw new BillingAddressRequiredError();
}

/** Return only the authenticated subscription's current unpaid hosted invoice. */
export async function ownedInvoiceRecoveryUrl(
  subscriptionId: string,
): Promise<string | null> {
  const context = stripeAccountContext();
  if (!context?.customerId) throw new BillingAccountHoldError();
  const subscription =
    await context.client.subscriptions.retrieve(subscriptionId);
  if (stripeObjectId(subscription.customer) !== context.customerId)
    throw new BillingAccountHoldError();
  if (["canceled", "incomplete_expired"].includes(subscription.status)) return null;
  const invoiceId = stripeObjectId(subscription.latest_invoice);
  if (!invoiceId) return null;
  const invoice = await context.client.invoices.retrieve(invoiceId);
  if (
    stripeObjectId(invoice.customer) !== context.customerId ||
    invoiceSubscriptionId(invoice) !== subscriptionId
  )
    throw new BillingAccountHoldError();
  if (invoice.status !== "open" || invoice.amount_remaining <= 0) return null;
  if (!invoice.hosted_invoice_url) throw new BillingAccountHoldError();
  const url = new URL(invoice.hosted_invoice_url);
  if (url.protocol !== "https:" || url.hostname !== "invoice.stripe.com")
    throw new BillingAccountHoldError();
  return url.href;
}

/** Only these risk events affect entitlements; partial refunds keep the paid period. */
export function isBillingRiskEvent(event: Stripe.Event): boolean {
  return [
    "charge.refunded",
    "charge.dispute.created",
    "radar.early_fraud_warning.created",
  ].includes(event.type);
}

export type BillingRisk = {
  charge: Stripe.Charge;
  customerId: string;
  subscriptionId?: string;
};

/** Resolve current charge → invoice payment → subscription within the signed account. */
export async function resolveBillingRisk(
  event: Stripe.Event,
  client: Stripe,
): Promise<BillingRisk | null> {
  if (!isBillingRiskEvent(event)) return null;
  const object = event.data.object;
  const chargeId =
    event.type === "charge.refunded"
      ? stripeObjectId(object)
      : "charge" in object
        ? stripeObjectId(object.charge)
        : undefined;
  if (!chargeId) throw new Error("Risk event has no charge");
  const charge = await client.charges.retrieve(chargeId);
  if (event.type === "charge.refunded" && !charge.refunded) return null;
  const customerId = stripeObjectId(charge.customer);
  if (!customerId) return null;
  // Top-ups have separate revocation and must never cancel a subscription.
  if (charge.metadata.shiori_kind === "usage_topup")
    return { charge, customerId };
  const legacy = charge as Stripe.Charge & { invoice?: unknown };
  let invoiceId = stripeObjectId(legacy.invoice);
  const paymentIntent = stripeObjectId(charge.payment_intent);
  if (!invoiceId && paymentIntent) {
    const payments = await client.invoicePayments.list({
      payment: { type: "payment_intent", payment_intent: paymentIntent },
      limit: 2,
    });
    const invoices = new Set(
      payments.data.map((p) => stripeObjectId(p.invoice)).filter(Boolean),
    );
    if (payments.has_more || invoices.size > 1)
      throw new Error("Ambiguous invoice payment for risk event");
    invoiceId = invoices.values().next().value;
  }
  if (!invoiceId) return { charge, customerId };
  const invoice = await client.invoices.retrieve(invoiceId);
  if (stripeObjectId(invoice.customer) !== customerId)
    throw new Error("Risk invoice customer mismatch");
  return { charge, customerId, subscriptionId: invoiceSubscriptionId(invoice) };
}

/** An inline top-up product needs its own reviewed tax code, not a guessed default. */
export function destinationTopupTaxCode(): { tax_code?: string } {
  const context = stripeAccountContext();
  if (!context || stripeAccountRole(context.accountId) !== "destination")
    return {};
  const code = process.env.STRIPE_DESTINATION_TOPUP_TAX_CODE;
  if (!code || !/^txcd_[0-9]{8}$/.test(code))
    throw new BillingAccountHoldError();
  return { tax_code: code };
}
