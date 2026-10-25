import { getPlanIdForStripePrice, isGrandfatheredProPrice } from "@/server/billing-plans";
import { getPrismaClient } from "@/server/prisma";
import { getServerStripeClient } from "@/server/stripe";
import type { BillingPlanId } from "@/lib/billing-plans";
import Stripe from "stripe";

// past_due stays entitled: Stripe is still retrying the invoice. unpaid and
// incomplete are not — retries are over, or the first invoice was never paid.
const ENTITLED_SUBSCRIPTION_STATUSES = new Set(["active", "trialing", "past_due"]);

// An upgrade invoice that is still open, or a subscription that is not in good
// standing, must not unlock a higher tier. The tier already paid for stays.
const UPGRADE_BLOCKING_SUBSCRIPTION_STATUSES = new Set([
  "incomplete",
  "incomplete_expired",
  "past_due",
  "unpaid",
  "canceled",
]);

type PaidPlan = BillingPlanId;

function toDate(unixSeconds: number | null | undefined): Date | null {
  if (!unixSeconds) {
    return null;
  }
  return new Date(unixSeconds * 1000);
}

function planRank(plan: string | null | undefined): number {
  if (plan === "scale") return 2;
  if (plan === "pro") return 1;
  return 0;
}

function asPaidPlan(plan: string | null | undefined): PaidPlan | null {
  if (plan === "pro" || plan === "scale") return plan;
  return null;
}

function higherPlan(current: PaidPlan | null, next: PaidPlan | null): PaidPlan | null {
  if (!next) return current;
  if (!current || planRank(next) > planRank(current)) return next;
  return current;
}

export function getPaidSubscriptionPlan(subscription: Stripe.Subscription): PaidPlan | null {
  if (!ENTITLED_SUBSCRIPTION_STATUSES.has(subscription.status)) {
    return null;
  }

  return pricePlanOnSubscription(subscription);
}

function priceIdOf(price: string | Stripe.Price | null | undefined): string | null {
  if (!price) return null;
  return typeof price === "string" ? price : price.id;
}

function planFromPriceId(priceId: string | null | undefined): PaidPlan | null {
  return asPaidPlan(getPlanIdForStripePrice(priceId));
}

/** Plan mapped from subscription item prices, ignoring subscription status. */
function pricePlanOnSubscription(subscription: Stripe.Subscription): PaidPlan | null {
  let plan: PaidPlan | null = null;
  for (const item of subscription.items?.data ?? []) {
    plan = higherPlan(plan, planFromPriceId(item.price?.id));
  }
  for (const item of subscription.pending_update?.subscription_items ?? []) {
    plan = higherPlan(plan, planFromPriceId(item.price?.id));
  }
  return plan;
}

function getSubscriptionPeriod(subscription: Stripe.Subscription): {
  currentPeriodStart: number | null;
  currentPeriodEnd: number | null;
} {
  const starts = subscription.items.data
    .map((item) => item.current_period_start)
    .filter((value): value is number => typeof value === "number");
  const ends = subscription.items.data
    .map((item) => item.current_period_end)
    .filter((value): value is number => typeof value === "number");

  return {
    currentPeriodStart: starts.length > 0 ? Math.min(...starts) : null,
    currentPeriodEnd: ends.length > 0 ? Math.max(...ends) : null,
  };
}

function customerIdOf(
  customer: string | { id: string } | null | undefined,
): string | null {
  if (!customer) return null;
  return typeof customer === "string" ? customer : customer.id;
}

type SubscriptionWriteOptions = {
  /** Persist this plan instead of the price currently on the subscription. */
  planOverride?: PaidPlan | "free";
  /** Only update a row still on this plan, so a paid grant cannot be overwritten. */
  onlyIfPlan?: string;
  /** Unpaid upgrades must not take over an App Store or affiliate entitlement. */
  restrictToStripeProvider?: boolean;
  statusOverride?: string;
  pendingPlan?: PaidPlan | null;
  pendingPlanInvoiceId?: string | null;
  /** Leave the paid billing window alone until the upgrade invoice is paid. */
  preserveBillingPeriod?: boolean;
};

export async function upsertSubscriptionState(
  subscription: Stripe.Subscription,
  options?: SubscriptionWriteOptions,
) {
  const prisma = getPrismaClient();
  const customerId = customerIdOf(subscription.customer);
  if (!customerId) return;

  const subscriptionId = subscription.id;
  const status = options?.statusOverride ?? subscription.status;
  const { currentPeriodStart, currentPeriodEnd } = getSubscriptionPeriod(subscription);
  // Portal cancellations set cancel_at_period_end (and usually cancel_at too).
  const cancelAt =
    subscription.cancel_at ?? (subscription.cancel_at_period_end ? currentPeriodEnd : null);

  const paidPlan =
    options?.planOverride === "free"
      ? null
      : options?.planOverride ?? getPaidSubscriptionPlan(subscription);
  const plan = options?.planOverride ?? paidPlan ?? "free";
  // Subscribers still on the pre-2026-10 Pro price keep 1080p, even if they
  // were past due when the flag was backfilled.
  const onGrandfatheredPrice =
    plan === "pro" && subscription.items.data.some((item) => isGrandfatheredProPrice(item.price?.id));

  // A non-paid Stripe state must not clobber an entitlement owned by the App
  // Store; only a paid Stripe grant may take over from an Apple subscription.
  const writingPaidGrant = plan === "pro" || plan === "scale";
  const providerFilter =
    options?.restrictToStripeProvider || !writingPaidGrant
      ? { OR: [{ subscription_provider: "stripe" }, { subscription_provider: null }] }
      : {};

  await prisma.user.updateMany({
    where: {
      stripe_customer_id: customerId,
      ...(options?.onlyIfPlan ? { plan: options.onlyIfPlan } : {}),
      ...providerFilter,
    },
    data: {
      plan,
      subscription_status: status,
      subscription_provider: "stripe",
      stripe_subscription_id: subscriptionId,
      ...(options?.preserveBillingPeriod
        ? {}
        : {
            billing_period_start: toDate(currentPeriodStart),
            billing_period_end: toDate(currentPeriodEnd),
          }),
      trial_ends_at: toDate(subscription.trial_end),
      subscription_cancel_at: toDate(cancelAt),
      pending_plan: options?.pendingPlan ?? null,
      pending_plan_invoice_id: options?.pendingPlanInvoiceId ?? null,
      // Grandfathered 1080p ends once the subscription is cancelled; a failed
      // payment (past_due/unpaid) keeps it so recovering customers retain it.
      ...(onGrandfatheredPrice
        ? { legacy_hd: true }
        : status === "canceled" || status === "incomplete_expired"
          ? { legacy_hd: false }
          : {}),
    },
  });
}

/**
 * Status to store while a higher tier is waiting on payment.
 * `unpaid` and `canceled` would drop access in billing even if `plan` stays
 * on the tier already paid for, so those stay `past_due` (access continues,
 * and the billing screen still asks for a payment update).
 */
function statusWhileUpgradeUnpaid(
  incoming: string,
  previous: string | null | undefined,
  keptPlan: PaidPlan | "free",
): string {
  if (keptPlan === "free") return incoming;
  if (incoming === "active" || incoming === "trialing" || incoming === "past_due") return incoming;
  if (previous === "trialing") return "trialing";
  return "past_due";
}

/** Paid, or nothing left to collect. Void and uncollectible invoices are not. */
function invoiceSettlesUpgrade(invoice: Stripe.Invoice | null | undefined): boolean {
  if (!invoice) return false;
  if (invoice.status === "void" || invoice.status === "uncollectible") return false;
  if (invoice.status === "paid") return true;
  return invoice.amount_due === 0;
}

async function resolveLatestInvoice(subscription: Stripe.Subscription): Promise<Stripe.Invoice | null> {
  const latest = subscription.latest_invoice;
  if (!latest) return null;
  if (typeof latest !== "string") return latest;
  return getServerStripeClient().invoices.retrieve(latest);
}

function invoiceIdOf(invoice: Stripe.Invoice | string | null | undefined): string | null {
  if (!invoice) return null;
  return typeof invoice === "string" ? invoice : invoice.id;
}

/**
 * `customer.subscription.updated`. Downgrades and same-tier changes are written
 * immediately. A higher tier is written only when its latest invoice is paid
 * (or amount_due is 0) and the subscription is active or trialing. Otherwise
 * the previous paid tier stays, and the unpaid tier is stored as pending.
 */
export async function syncSubscriptionUpdate(subscription: Stripe.Subscription) {
  const prisma = getPrismaClient();
  const customerId = customerIdOf(subscription.customer);
  if (!customerId) {
    await upsertSubscriptionState(subscription);
    return;
  }

  const user = await prisma.user.findFirst({
    where: { stripe_customer_id: customerId },
    select: {
      plan: true,
      subscription_status: true,
      subscription_provider: true,
    },
  });

  // No linked user yet (checkout links the customer before the first paid
  // event). Apply the subscription as-is; checkout.session.completed does too.
  if (!user) {
    await upsertSubscriptionState(subscription);
    return;
  }

  const targetPlan = pricePlanOnSubscription(subscription);
  const currentPlan = asPaidPlan(user.plan) ?? "free";
  const isUpgrade = Boolean(targetPlan && planRank(targetPlan) > planRank(currentPlan));

  if (!isUpgrade) {
    await upsertSubscriptionState(subscription);
    return;
  }

  // App Store and affiliate Pro stay put until the upgrade invoice is paid.
  // invoice.paid grants the higher tier and switches the provider then.
  if (user.subscription_provider === "apple" || user.subscription_provider === "affiliate") {
    const invoice = await resolveLatestInvoice(subscription);
    if (
      invoiceSettlesUpgrade(invoice) &&
      !UPGRADE_BLOCKING_SUBSCRIPTION_STATUSES.has(subscription.status)
    ) {
      await upsertSubscriptionState(subscription, { planOverride: targetPlan! });
    }
    return;
  }

  const invoice = await resolveLatestInvoice(subscription);
  const blockedBySubscription = UPGRADE_BLOCKING_SUBSCRIPTION_STATUSES.has(subscription.status);
  const blockedByInvoice = !invoiceSettlesUpgrade(invoice);

  if (!blockedBySubscription && !blockedByInvoice) {
    await upsertSubscriptionState(subscription, { planOverride: targetPlan! });
    return;
  }

  const keptPlan = currentPlan === "free" ? "free" : currentPlan;
  await upsertSubscriptionState(subscription, {
    planOverride: keptPlan,
    onlyIfPlan: user.plan,
    restrictToStripeProvider: true,
    preserveBillingPeriod: true,
    statusOverride: statusWhileUpgradeUnpaid(subscription.status, user.subscription_status, keptPlan),
    pendingPlan: targetPlan!,
    pendingPlanInvoiceId: invoiceIdOf(invoice) ?? invoiceIdOf(subscription.latest_invoice),
  });
}

function priceIdFromInvoiceLine(line: Stripe.InvoiceLineItem): string | null {
  const priced = line.pricing?.price_details?.price;
  const fromPricing = priceIdOf(priced);
  if (fromPricing) return fromPricing;
  // Webhook endpoints still on an older API version send `price` on the line.
  const legacy = (line as Stripe.InvoiceLineItem & { price?: string | Stripe.Price | null }).price;
  return priceIdOf(legacy);
}

/** Highest plan on a non-credit line. Proration credits (negative amounts) are the old price. */
function highestPlanOnInvoice(invoice: Stripe.Invoice): PaidPlan | null {
  let plan: PaidPlan | null = null;
  for (const line of invoice.lines?.data ?? []) {
    if (typeof line.amount === "number" && line.amount < 0) continue;
    plan = higherPlan(plan, planFromPriceId(priceIdFromInvoiceLine(line)));
  }
  return plan;
}

function invoiceSubscriptionId(invoice: Stripe.Invoice): string | null {
  const parentSub = invoice.parent?.subscription_details?.subscription;
  if (typeof parentSub === "string") return parentSub;
  if (parentSub && typeof parentSub === "object" && "id" in parentSub) return parentSub.id;

  const legacy = (invoice as Stripe.Invoice & { subscription?: string | Stripe.Subscription | null }).subscription;
  if (typeof legacy === "string") return legacy;
  if (legacy && typeof legacy === "object") return legacy.id;

  for (const line of invoice.lines?.data ?? []) {
    if (typeof line.subscription === "string") return line.subscription;
    if (line.subscription && typeof line.subscription === "object") return line.subscription.id;
  }
  return null;
}

function plansStrictlyBelow(plan: PaidPlan): string[] {
  return plan === "scale" ? ["free", "pro"] : ["free"];
}

/**
 * `invoice.paid` and `invoice.payment_succeeded`. Grants a pending higher tier,
 * or the tier on the invoice when that event arrives before subscription.updated.
 * Applying the same invoice twice does not change a plan that is already granted.
 */
export async function applyPaidUpgradeInvoice(invoice: Stripe.Invoice) {
  if (!invoiceSettlesUpgrade(invoice)) return;

  const prisma = getPrismaClient();
  const customerId = customerIdOf(invoice.customer);
  if (!customerId || !invoice.id) return;

  const user = await prisma.user.findFirst({
    where: { stripe_customer_id: customerId },
    select: {
      plan: true,
      pending_plan: true,
      pending_plan_invoice_id: true,
      subscription_provider: true,
    },
  });
  if (!user || user.subscription_provider === "apple") return;

  const invoicePlan = highestPlanOnInvoice(invoice);
  const pendingPlan = asPaidPlan(user.pending_plan);
  // Only the invoice that subscription.updated stored may grant a pending tier
  // when the invoice lines themselves do not name that price. A different paid
  // invoice (a renewal of the current tier) must not.
  const pendingMatchesInvoice =
    pendingPlan !== null && user.pending_plan_invoice_id === invoice.id;

  let grant: PaidPlan | null = null;
  if (invoicePlan && planRank(invoicePlan) > planRank(user.plan)) {
    grant = invoicePlan;
  } else if (pendingMatchesInvoice && pendingPlan && planRank(pendingPlan) > planRank(user.plan)) {
    grant = pendingPlan;
  }

  if (!grant) {
    if (user.pending_plan_invoice_id === invoice.id) {
      await prisma.user.updateMany({
        where: { stripe_customer_id: customerId, pending_plan_invoice_id: invoice.id },
        data: { pending_plan: null, pending_plan_invoice_id: null },
      });
    }
    return;
  }

  const subscriptionId = invoiceSubscriptionId(invoice);
  await prisma.user.updateMany({
    where: {
      stripe_customer_id: customerId,
      plan: { in: plansStrictlyBelow(grant) },
      OR: [
        { subscription_provider: "stripe" },
        { subscription_provider: null },
        { subscription_provider: "affiliate" },
      ],
    },
    data: {
      plan: grant,
      pending_plan: null,
      pending_plan_invoice_id: null,
      subscription_provider: "stripe",
      subscription_status: "active",
      ...(subscriptionId ? { stripe_subscription_id: subscriptionId } : {}),
    },
  });
}

/**
 * `invoice.payment_failed`. Records the unpaid higher tier and leaves the plan
 * the customer already paid for in place. A later paid event can still grant it.
 */
export async function noteUpgradePaymentFailed(invoice: Stripe.Invoice) {
  if (invoice.status === "void") return;

  const prisma = getPrismaClient();
  const customerId = customerIdOf(invoice.customer);
  if (!customerId || !invoice.id) return;

  const user = await prisma.user.findFirst({
    where: { stripe_customer_id: customerId },
    select: {
      plan: true,
      pending_plan: true,
      pending_plan_invoice_id: true,
      subscription_provider: true,
    },
  });
  if (!user || user.subscription_provider === "apple" || user.subscription_provider === "affiliate") {
    return;
  }

  const invoicePlan = highestPlanOnInvoice(invoice);
  const pendingPlan = asPaidPlan(user.pending_plan);
  const failedUpgrade =
    (invoicePlan && planRank(invoicePlan) > planRank(user.plan) ? invoicePlan : null) ??
    (user.pending_plan_invoice_id === invoice.id ? pendingPlan : null);

  // Already on this tier (or a higher one): a failed charge must not revoke it.
  if (!failedUpgrade || planRank(failedUpgrade) <= planRank(user.plan)) return;

  await prisma.user.updateMany({
    where: {
      stripe_customer_id: customerId,
      plan: user.plan,
      OR: [{ subscription_provider: "stripe" }, { subscription_provider: null }],
    },
    data: {
      pending_plan: failedUpgrade,
      pending_plan_invoice_id: invoice.id,
    },
  });
}

/**
 * Rows written before past_due kept its plan still say `free`. The next
 * subscription webhook would fix them; this applies the same sync on read so
 * a customer already inside the retry window is not stuck on Free.
 * Returns true when the stored subscription was rewritten and the caller
 * should reload billing.
 */
export async function restorePastDueEntitlement(userId: string): Promise<boolean> {
  const prisma = getPrismaClient();
  const user = await prisma.user.findUnique({
    where: { id: userId },
    select: {
      plan: true,
      subscription_status: true,
      subscription_provider: true,
      stripe_subscription_id: true,
      pending_plan: true,
    },
  });
  if (
    !user?.stripe_subscription_id ||
    user.subscription_provider !== "stripe" ||
    user.subscription_status !== "past_due" ||
    user.plan === "pro" ||
    user.plan === "scale" ||
    user.pending_plan === "pro" ||
    user.pending_plan === "scale"
  ) {
    return false;
  }

  const subscription = await getServerStripeClient().subscriptions.retrieve(user.stripe_subscription_id);
  await upsertSubscriptionState(subscription);
  return true;
}
