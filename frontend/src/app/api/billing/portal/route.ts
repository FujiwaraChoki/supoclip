import { NextResponse } from "next/server";
import { headers } from "next/headers";
import { auth } from "@/lib/auth";
import prisma from "@/lib/prisma";
import { monetizationEnabled } from "@/lib/monetization";
import { getStripeClient } from "@/lib/stripe";
import Stripe from "stripe";

function isMissingStripeCustomer(error: unknown): boolean {
  return (
    error instanceof Stripe.errors.StripeInvalidRequestError &&
    error.code === "resource_missing" &&
    typeof error.message === "string" &&
    error.message.includes("No such customer")
  );
}

async function createStripeCustomerForUser(user: {
  id: string;
  email: string;
  name: string | null;
}): Promise<string> {
  const stripe = getStripeClient();
  const customer = await stripe.customers.create({
    email: user.email,
    name: user.name || undefined,
    metadata: { userId: user.id },
  });

  await prisma.user.update({
    where: { id: user.id },
    data: { stripe_customer_id: customer.id },
  });

  return customer.id;
}

async function createPortalSession(customerId: string) {
  const stripe = getStripeClient();
  const appUrl = process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3000";
  return stripe.billingPortal.sessions.create({
    customer: customerId,
    return_url: `${appUrl}/settings`,
  });
}

export async function POST() {
  if (!monetizationEnabled) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id || !session.user.email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const user = await prisma.user.findUnique({
    where: { id: session.user.id },
    select: { id: true, email: true, name: true, stripe_customer_id: true },
  });
  let customerId = user?.stripe_customer_id || null;

  if (!customerId) {
    const fallbackUrl = process.env.STRIPE_CUSTOMER_PORTAL_URL;
    if (fallbackUrl) {
      return NextResponse.json({ url: fallbackUrl });
    }
    if (!user) {
      return NextResponse.json({ error: "No Stripe customer found" }, { status: 400 });
    }
    customerId = await createStripeCustomerForUser(user);
  }

  try {
    const portalSession = await createPortalSession(customerId);
    return NextResponse.json({ url: portalSession.url });
  } catch (error) {
    if (user && isMissingStripeCustomer(error)) {
      const newCustomerId = await createStripeCustomerForUser(user);
      const portalSession = await createPortalSession(newCustomerId);
      return NextResponse.json({ url: portalSession.url });
    }

    console.error("Failed to create Stripe billing portal session", error);
    return NextResponse.json(
      { error: "Unable to open billing portal" },
      { status: 500 }
    );
  }
}
