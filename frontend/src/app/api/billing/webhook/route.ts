import { NextResponse } from "next/server";
import { restoreAffiliateProIfApproved } from "@/server/affiliates";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";
import { monetizationEnabled } from "@/lib/monetization";
import { fetchBackend } from "@/server/backend-api";
import type { BillingInterval } from "@/lib/billing-plans";
import { intervalFromPeriod, WebhookAnalytics } from "@/server/analytics";
import { getPlanIdForStripePrice, hasAnyConfiguredStripePrice } from "@/server/billing-plans";
import { getPrismaClient } from "@/server/prisma";
import { getServerStripeClient } from "@/server/stripe";
import {
  applyPaidUpgradeInvoice,
  getPaidSubscriptionPlan,
  noteUpgradePaymentFailed,
  syncSubscriptionUpdate,
  upsertSubscriptionState,
} from "@/server/stripe-subscription-sync";
import Stripe from "stripe";

type SubscriptionEmailEvent = "subscribed" | "unsubscribed" | "cancellation_scheduled";

async function sendBackendSubscriptionEmail(userId: string, event: SubscriptionEmailEvent) {
  const response = await fetchBackend("/billing/subscription-email", {
    method: "POST",
    userId,
    extraHeaders: {
      ...buildBackendAuthHeaders(userId),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ event }),
    cache: "no-store",
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(
      `Backend subscription email failed with ${response.status}: ${detail || "unknown error"}`
    );
  }
}

async function sendSubscriptionEmailBestEffort(
  userId: string,
  event: SubscriptionEmailEvent
) {
  try {
    await sendBackendSubscriptionEmail(userId, event);
  } catch (error) {
    console.error("Subscription email side effect failed", {
      userId,
      event,
      error,
    });
  }
}

function subscriptionInterval(subscription: Stripe.Subscription): BillingInterval | null {
  const item = subscription.items?.data?.[0];
  const interval = item?.price?.recurring?.interval;
  if (interval === "month" || interval === "year") return interval;
  return intervalFromPeriod(item?.current_period_start, item?.current_period_end);
}

/** The plan a subscription's prices map to, whatever its status (for canceled ones). */
function subscriptionPricePlan(subscription: Stripe.Subscription): string | null {
  const plans = (subscription.items?.data ?? []).map((item) => getPlanIdForStripePrice(item.price?.id));
  return plans.includes("scale") ? "scale" : plans.includes("pro") ? "pro" : null;
}

/** Only renewals: the first invoice is subscription_started, and plan changes are not renewals. */
async function noteSubscriptionRenewal(invoice: Stripe.Invoice, analytics: WebhookAnalytics) {
  if (invoice.billing_reason !== "subscription_cycle" || !invoice.id) return;
  const customerId = typeof invoice.customer === "string" ? invoice.customer : invoice.customer?.id;
  if (!customerId) return;
  try {
    const user = await getPrismaClient().user.findFirst({
      where: { stripe_customer_id: customerId },
      select: { id: true, plan: true, subscription_provider: true },
    });
    if (!user || user.subscription_provider === "apple") return;
    const period = invoice.lines?.data?.[0]?.period;
    analytics.add(
      {
        event: "subscription_renewed",
        distinctId: user.id,
        properties: {
          plan: user.plan,
          interval: intervalFromPeriod(period?.start, period?.end),
          provider: "stripe",
        },
        set: { plan: user.plan, subscription_status: "active" },
      },
      // invoice.paid and invoice.payment_succeeded both arrive for one renewal.
      { claimKey: `subscription_renewed:${invoice.id}` },
    );
  } catch (error) {
    // Analytics only: a failed lookup must not fail the webhook.
    console.warn("Could not record subscription renewal", error instanceof Error ? error.name : "error");
  }
}

/** Claims an analytics fact in the webhook dedup table; false when it was already sent. */
async function claimAnalyticsKey(key: string): Promise<boolean> {
  try {
    await getPrismaClient().stripeWebhookEvent.create({ data: { id: `posthog:${key}`, type: "posthog" } });
    return true;
  } catch (error) {
    if ((error as { code?: string }).code === "P2002") return false;
    throw error;
  }
}

async function handleCheckoutCompleted(session: Stripe.Checkout.Session, analytics: WebhookAnalytics) {
  const prisma = getPrismaClient();
  if (session.mode !== "subscription") {
    return;
  }

  const customerId = session.customer ? String(session.customer) : null;
  const subscriptionId = session.subscription ? String(session.subscription) : null;
  if (!customerId || !subscriptionId) {
    return;
  }

  let userId = session.metadata?.userId ?? null;
  if (!userId) {
    const customerEmail = session.customer_details?.email || session.customer_email || undefined;
    const existingUser = await prisma.user.findFirst({
      where: customerEmail
        ? {
            OR: [{ stripe_customer_id: customerId }, { email: customerEmail }],
          }
        : { stripe_customer_id: customerId },
      select: { id: true },
    });
    userId = existingUser?.id ?? null;
  }

  if (userId) {
    await prisma.user.update({
      where: { id: userId },
      data: {
        stripe_customer_id: customerId,
        stripe_subscription_id: subscriptionId,
      },
    });
  }

  const stripe = getServerStripeClient();
  const subscription = await stripe.subscriptions.retrieve(subscriptionId);
  await upsertSubscriptionState(subscription);

  const paidPlan = getPaidSubscriptionPlan(subscription);
  if (userId && paidPlan) {
    await sendSubscriptionEmailBestEffort(userId, "subscribed");
    analytics.add({
      event: "subscription_started",
      distinctId: userId,
      properties: {
        plan: paidPlan,
        interval: subscriptionInterval(subscription),
        provider: "stripe",
        trial: subscription.status === "trialing",
        has_creator_code: Boolean(session.metadata?.creator_code),
      },
      set: { plan: paidPlan, subscription_status: subscription.status },
    });
  }
}

async function handleSubscriptionDeleted(subscription: Stripe.Subscription, analytics: WebhookAnalytics) {
  const prisma = getPrismaClient();
  const customerId = typeof subscription.customer === "string" ? subscription.customer : subscription.customer.id;
  const user = await prisma.user.findFirst({
    where: { stripe_customer_id: customerId },
    select: { id: true, subscription_provider: true },
  });

  const result = await prisma.user.updateMany({
    where: {
      stripe_customer_id: customerId,
      OR: [{ subscription_provider: "stripe" }, { subscription_provider: null }],
    },
    data: {
      plan: "free",
      subscription_status: "canceled",
      subscription_provider: null,
      stripe_subscription_id: null,
      billing_period_start: null,
      billing_period_end: null,
      trial_ends_at: null,
      subscription_cancel_at: null,
    },
  });

  if (user?.id && result.count > 0) {
    // Approved creators fall back to their free Pro; they haven't lost access, so no "unsubscribed" email.
    const keptCreatorPro = await restoreAffiliateProIfApproved(prisma, user.id);
    analytics.add({
      event: "subscription_canceled",
      distinctId: user.id,
      properties: {
        plan: subscriptionPricePlan(subscription),
        interval: subscriptionInterval(subscription),
        provider: "stripe",
        kept_creator_pro: keptCreatorPro,
      },
      set: keptCreatorPro
        ? { plan: "pro", subscription_status: "active" }
        : { plan: "free", subscription_status: "canceled" },
    });
    if (keptCreatorPro) return;
    await sendSubscriptionEmailBestEffort(user.id, "unsubscribed");
  }
}

const LIVE_SUBSCRIPTION_STATUSES = ["active", "trialing", "past_due"];

function hasScheduledCancellation(subscription: Pick<Stripe.Subscription, "cancel_at_period_end" | "cancel_at">) {
  return subscription.cancel_at_period_end || subscription.cancel_at != null;
}

function isNewlyScheduledCancellation(
  subscription: Stripe.Subscription,
  previousAttributes: Partial<Stripe.Subscription> | undefined,
) {
  if (!previousAttributes || !LIVE_SUBSCRIPTION_STATUSES.includes(subscription.status)) {
    return false;
  }
  // Stripe also sends updates for discounts, plan changes, and resumed subscriptions.
  // Only the transition into a scheduled cancellation should send this email.
  return hasScheduledCancellation(subscription)
    && !hasScheduledCancellation({ ...subscription, ...previousAttributes });
}

/** Returns the user that was emailed, or null when nothing was sent. */
async function sendCancellationScheduledEmail(subscription: Stripe.Subscription): Promise<string | null> {
  if (
    !LIVE_SUBSCRIPTION_STATUSES.includes(subscription.status)
    || !hasScheduledCancellation(subscription)
  ) {
    return null;
  }

  const customerId = typeof subscription.customer === "string"
    ? subscription.customer
    : subscription.customer.id;
  const user = await getPrismaClient().user.findFirst({
    where: {
      stripe_customer_id: customerId,
      stripe_subscription_id: subscription.id,
      subscription_provider: "stripe",
      subscription_cancel_at: { not: null },
    },
    select: { id: true },
  });
  if (!user) return null;

  // Let Stripe retry delivery failures. The event record suppresses successful
  // redeliveries, and previous_attributes still identifies the transition on retry.
  await sendBackendSubscriptionEmail(user.id, "cancellation_scheduled");
  return user.id;
}

export async function POST(request: Request) {
  if (!monetizationEnabled) {
    return NextResponse.json({ ok: true });
  }

  const webhookSecret = process.env.STRIPE_WEBHOOK_SECRET;
  if (!webhookSecret) {
    return NextResponse.json({ error: "Webhook secret is not configured" }, { status: 500 });
  }

  if (!hasAnyConfiguredStripePrice()) {
    return NextResponse.json({ error: "No Stripe prices are configured" }, { status: 500 });
  }

  const signature = request.headers.get("stripe-signature");
  if (!signature) {
    return NextResponse.json({ error: "Missing Stripe signature" }, { status: 400 });
  }

  const payload = await request.text();
  const stripe = getServerStripeClient();

  let event: Stripe.Event;
  try {
    event = stripe.webhooks.constructEvent(payload, signature, webhookSecret);
  } catch {
    return NextResponse.json({ error: "Invalid signature" }, { status: 400 });
  }

  try {
    await getPrismaClient().stripeWebhookEvent.create({
      data: {
        id: event.id,
        type: event.type,
      },
    });
  } catch (error) {
    const knownError = error as { code?: string };
    if (knownError.code === "P2002") {
      return NextResponse.json({ ok: true });
    }
    throw error;
  }

  const analytics = new WebhookAnalytics(
    event.id,
    typeof event.created === "number" ? new Date(event.created * 1000) : undefined,
  );

  try {
    if (event.type === "checkout.session.completed") {
      await handleCheckoutCompleted(event.data.object as Stripe.Checkout.Session, analytics);
    }

    if (event.type === "customer.subscription.updated") {
      const subscription = event.data.object as Stripe.Subscription;
      if (isNewlyScheduledCancellation(subscription, event.data.previous_attributes)) {
        // A retry after a failed email can arrive after the customer resumed. Sync and
        // decide from the live subscription so the old snapshot can't overwrite that.
        const current = await stripe.subscriptions.retrieve(subscription.id);
        await syncSubscriptionUpdate(current);
        const userId = await sendCancellationScheduledEmail(current);
        if (userId) {
          const plan = getPaidSubscriptionPlan(current);
          analytics.add({
            event: "subscription_cancel_scheduled",
            distinctId: userId,
            properties: { plan, interval: subscriptionInterval(current), provider: "stripe" },
            set: { ...(plan ? { plan } : {}), subscription_status: current.status },
          });
        }
      } else {
        await syncSubscriptionUpdate(subscription);
      }
    }

    // Enable these on the Stripe webhook endpoint. invoice.paid covers
    // out-of-band payments; invoice.payment_succeeded covers a successful charge.
    // Both grant a higher tier that subscription.updated left pending.
    if (event.type === "invoice.paid" || event.type === "invoice.payment_succeeded") {
      await applyPaidUpgradeInvoice(event.data.object as Stripe.Invoice);
      await noteSubscriptionRenewal(event.data.object as Stripe.Invoice, analytics);
    }

    if (event.type === "invoice.payment_failed") {
      await noteUpgradePaymentFailed(event.data.object as Stripe.Invoice);
    }

    if (event.type === "customer.subscription.deleted") {
      await handleSubscriptionDeleted(event.data.object as Stripe.Subscription, analytics);
    }
  } catch (error) {
    await getPrismaClient().stripeWebhookEvent.delete({ where: { id: event.id } }).catch(() => {});
    throw error;
  }

  // Only after the handler succeeded, so a delivery that Stripe retries is counted once.
  await analytics.flush(claimAnalyticsKey);
  return NextResponse.json({ ok: true });
}
