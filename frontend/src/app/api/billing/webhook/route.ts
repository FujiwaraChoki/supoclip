import { NextResponse } from "next/server";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";
import { monetizationEnabled } from "@/lib/monetization";
import { fetchBackend } from "@/server/backend-api";
import { getPrismaClient } from "@/server/prisma";
import { getServerStripeClient } from "@/server/stripe";
import Stripe from "stripe";

type SubscriptionEmailEvent = "subscribed" | "unsubscribed";

function toDate(unixSeconds: number | null | undefined): Date | null {
  if (!unixSeconds) {
    return null;
  }
  return new Date(unixSeconds * 1000);
}

function isProSubscription(subscription: Stripe.Subscription, expectedPriceId: string): boolean {
  const hasExpectedPrice = subscription.items.data.some(
    (item) => item.price?.id === expectedPriceId
  );

  return hasExpectedPrice && (subscription.status === "active" || subscription.status === "trialing");
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

async function upsertSubscriptionState(subscription: Stripe.Subscription, expectedPriceId: string) {
  const prisma = getPrismaClient();
  const customerId = typeof subscription.customer === "string" ? subscription.customer : subscription.customer.id;
  const subscriptionId = subscription.id;
  const status = subscription.status;
  const { currentPeriodStart, currentPeriodEnd } = getSubscriptionPeriod(subscription);

  const plan = isProSubscription(subscription, expectedPriceId) ? "pro" : "free";

  await prisma.user.updateMany({
    where: { stripe_customer_id: customerId },
    data: {
      plan,
      subscription_status: status,
      stripe_subscription_id: subscriptionId,
      billing_period_start: toDate(currentPeriodStart),
      billing_period_end: toDate(currentPeriodEnd),
      trial_ends_at: toDate(subscription.trial_end),
    },
  });
}

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

async function handleCheckoutCompleted(session: Stripe.Checkout.Session, expectedPriceId: string) {
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
  await upsertSubscriptionState(subscription, expectedPriceId);

  if (userId && isProSubscription(subscription, expectedPriceId)) {
    await sendSubscriptionEmailBestEffort(userId, "subscribed");
  }
}

async function handleSubscriptionDeleted(subscription: Stripe.Subscription) {
  const prisma = getPrismaClient();
  const customerId = typeof subscription.customer === "string" ? subscription.customer : subscription.customer.id;
  const user = await prisma.user.findFirst({
    where: { stripe_customer_id: customerId },
    select: { id: true },
  });

  await prisma.user.updateMany({
    where: { stripe_customer_id: customerId },
    data: {
      plan: "free",
      subscription_status: "canceled",
      stripe_subscription_id: null,
      billing_period_start: null,
      billing_period_end: null,
      trial_ends_at: null,
    },
  });

  if (user?.id) {
    await sendSubscriptionEmailBestEffort(user.id, "unsubscribed");
  }
}

export async function POST(request: Request) {
  if (!monetizationEnabled) {
    return NextResponse.json({ ok: true });
  }

  const webhookSecret = process.env.STRIPE_WEBHOOK_SECRET;
  if (!webhookSecret) {
    return NextResponse.json({ error: "Webhook secret is not configured" }, { status: 500 });
  }

  const expectedPriceId = process.env.STRIPE_PRICE_ID;
  if (!expectedPriceId) {
    return NextResponse.json({ error: "STRIPE_PRICE_ID is not configured" }, { status: 500 });
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

  try {
    if (event.type === "checkout.session.completed") {
      await handleCheckoutCompleted(event.data.object as Stripe.Checkout.Session, expectedPriceId);
    }

    if (event.type === "customer.subscription.updated") {
      await upsertSubscriptionState(event.data.object as Stripe.Subscription, expectedPriceId);
    }

    if (event.type === "customer.subscription.deleted") {
      await handleSubscriptionDeleted(event.data.object as Stripe.Subscription);
    }
  } catch (error) {
    await getPrismaClient().stripeWebhookEvent.delete({ where: { id: event.id } }).catch(() => {});
    throw error;
  }

  return NextResponse.json({ ok: true });
}
