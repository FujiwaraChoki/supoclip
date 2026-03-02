import { NextResponse } from "next/server";
import { headers } from "next/headers";
import { auth } from "@/lib/auth";
import prisma from "@/lib/prisma";
import { monetizationEnabled } from "@/lib/monetization";
import { getStripeClient } from "@/lib/stripe";

export async function POST() {
  if (!monetizationEnabled) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id || !session.user.email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const priceId = process.env.STRIPE_PRICE_ID;
  if (!priceId) {
    const fallbackUrl = process.env.STRIPE_CHECKOUT_URL;
    if (!fallbackUrl) {
      return NextResponse.json(
        { error: "STRIPE_PRICE_ID or STRIPE_CHECKOUT_URL must be configured" },
        { status: 500 }
      );
    }
    return NextResponse.json({ url: fallbackUrl });
  }

  const stripe = getStripeClient();
  const appUrl = process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3000";

  const user = await prisma.user.findUnique({
    where: { id: session.user.id },
    select: { stripe_customer_id: true },
  });

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

  const checkoutSession = await stripe.checkout.sessions.create({
    mode: "subscription",
    customer: customerId,
    metadata: {
      userId: session.user.id,
    },
    line_items: [{ price: priceId, quantity: 1 }],
    success_url: `${appUrl}/settings?billing=success`,
    cancel_url: `${appUrl}/settings?billing=cancelled`,
  });

  if (!checkoutSession.url) {
    return NextResponse.json({ error: "Unable to create checkout session" }, { status: 500 });
  }

  return NextResponse.json({ url: checkoutSession.url });
}
