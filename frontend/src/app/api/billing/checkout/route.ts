import { NextResponse } from "next/server";
import { headers } from "next/headers";
import { auth } from "@/lib/auth";
import prisma from "@/lib/prisma";
import { monetizationEnabled } from "@/lib/monetization";
import { getStripeClient } from "@/lib/stripe";
import { getServerBillingPlan } from "@/server/billing-plans";
import { findCreatorOffer, INVALID_CREATOR_CODE, type CreatorOffer } from "@/server/affiliates";
import type Stripe from "stripe";

const APP_STORE_MANAGED_MESSAGE = "Your subscription is managed through the App Store";
const PAID_SUBSCRIPTION_STATUSES = new Set(["active", "trialing"]);

export async function POST(request: Request) {
  if (!monetizationEnabled) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id || !session.user.email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let requestedPlan = "pro";
  let enteredCode: string | null = null;
  try {
    const body = await request.json();
    if (typeof body?.plan === "string") {
      requestedPlan = body.plan;
    }
    if (typeof body?.code === "string" && body.code.trim()) {
      enteredCode = body.code.trim();
    }
  } catch {
    requestedPlan = "pro";
  }

  const billingPlan = getServerBillingPlan(requestedPlan);
  if (!billingPlan) {
    return NextResponse.json({ error: "Unknown billing plan" }, { status: 400 });
  }

  const user = await prisma.user.findUnique({
    where: { id: session.user.id },
    select: {
      stripe_customer_id: true,
      stripe_subscription_id: true,
      subscription_provider: true,
      subscription_status: true,
    },
  });

  if (
    user?.subscription_provider === "apple" &&
    PAID_SUBSCRIPTION_STATUSES.has(user.subscription_status)
  ) {
    return NextResponse.json(
      { error: APP_STORE_MANAGED_MESSAGE },
      { status: 409 }
    );
  }

  const priceId = billingPlan.priceId;
  if (!priceId) {
    const fallbackUrl = billingPlan.id === "pro" ? process.env.STRIPE_CHECKOUT_URL : null;
    if (!fallbackUrl) {
      return NextResponse.json(
        { error: `Stripe price is not configured for ${billingPlan.id}` },
        { status: 500 }
      );
    }
    return NextResponse.json({ url: fallbackUrl });
  }

  const stripe = getStripeClient();
  const appUrl = process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3107";

  // Existing subscribers change plans in place; a second Checkout would bill them twice.
  if (
    user?.stripe_customer_id &&
    user.stripe_subscription_id &&
    PAID_SUBSCRIPTION_STATUSES.has(user.subscription_status)
  ) {
    return createPlanChangeSession(stripe, {
      customerId: user.stripe_customer_id,
      subscriptionId: user.stripe_subscription_id,
      priceId,
      appUrl,
    });
  }

  let customerId = user?.stripe_customer_id || null;
  if (!customerId) {
    const customer = await stripe.customers.create({
      email: session.user.email,
      name: session.user.name || undefined,
      metadata: { userId: session.user.id },
    });
    customerId = customer.id;

    await prisma.user.update({
      where: { id: session.user.id },
      data: { stripe_customer_id: customerId },
    });
  }

  // A typed code must be valid; a code from the signup ?ref= link is applied when it matches one.
  let offer: CreatorOffer | null = null;
  if (enteredCode) {
    offer = await findCreatorOffer(prisma, enteredCode);
    if (!offer) {
      return NextResponse.json({ error: "That creator code isn't valid", code: INVALID_CREATOR_CODE }, { status: 400 });
    }
    if (offer.affiliateUserId === session.user.id) {
      return NextResponse.json({ error: "You can't use your own creator code", code: INVALID_CREATOR_CODE }, { status: 400 });
    }
  } else {
    const acquisition = await prisma.userAcquisition.findUnique({
      where: { user_id: session.user.id },
      select: { ref: true },
    });
    if (acquisition?.ref) {
      offer = await findCreatorOffer(prisma, acquisition.ref);
      if (offer?.affiliateUserId === session.user.id) offer = null;
    }
  }

  const checkoutSession = await stripe.checkout.sessions.create({
    mode: "subscription",
    customer: customerId,
    metadata: {
      userId: session.user.id,
      plan: billingPlan.id,
      ...(offer ? { creator_code: offer.code } : {}),
    },
    line_items: [{ price: priceId, quantity: 1 }],
    ...(offer ? { discounts: [{ promotion_code: offer.promotionCodeId }] } : {}),
    success_url: `${appUrl}/settings?billing=success`,
    cancel_url: `${appUrl}/settings?billing=cancelled`,
  });

  if (!checkoutSession.url) {
    return NextResponse.json({ error: "Unable to create checkout session" }, { status: 500 });
  }

  return NextResponse.json({ url: checkoutSession.url });
}

async function createPlanChangeSession(
  stripe: Stripe,
  {
    customerId,
    subscriptionId,
    priceId,
    appUrl,
  }: { customerId: string; subscriptionId: string; priceId: string; appUrl: string }
) {
  const subscription = await stripe.subscriptions.retrieve(subscriptionId);
  const item = subscription.items.data[0];
  if (!item) {
    return NextResponse.json({ error: "Subscription has no items" }, { status: 500 });
  }
  if (item.price.id === priceId) {
    return NextResponse.json({ error: "You're already on this plan" }, { status: 409 });
  }

  const returnUrl = `${appUrl}/settings`;
  try {
    const portalSession = await stripe.billingPortal.sessions.create({
      customer: customerId,
      return_url: returnUrl,
      flow_data: {
        type: "subscription_update_confirm",
        subscription_update_confirm: {
          subscription: subscriptionId,
          items: [{ id: item.id, price: priceId, quantity: 1 }],
        },
        after_completion: {
          type: "redirect",
          redirect: { return_url: `${appUrl}/settings?billing=upgraded` },
        },
      },
    });
    return NextResponse.json({ url: portalSession.url });
  } catch (error) {
    // Portal configs without plan switching enabled reject the flow; the plain
    // portal still lets the customer manage their subscription.
    console.error("Failed to create plan change portal session", error);
    const portalSession = await stripe.billingPortal.sessions.create({
      customer: customerId,
      return_url: returnUrl,
    });
    return NextResponse.json({ url: portalSession.url });
  }
}
