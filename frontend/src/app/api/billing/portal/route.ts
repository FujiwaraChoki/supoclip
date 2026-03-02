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
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const user = await prisma.user.findUnique({
    where: { id: session.user.id },
    select: { stripe_customer_id: true },
  });
  const customerId = user?.stripe_customer_id || null;

  if (!customerId) {
    const fallbackUrl = process.env.STRIPE_CUSTOMER_PORTAL_URL;
    if (!fallbackUrl) {
      return NextResponse.json({ error: "No Stripe customer found" }, { status: 400 });
    }
    return NextResponse.json({ url: fallbackUrl });
  }

  const stripe = getStripeClient();
  const appUrl = process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3000";
  const portalSession = await stripe.billingPortal.sessions.create({
    customer: customerId,
    return_url: `${appUrl}/settings`,
  });

  return NextResponse.json({ url: portalSession.url });
}
