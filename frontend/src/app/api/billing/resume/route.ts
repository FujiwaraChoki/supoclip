import { NextResponse } from "next/server";
import { headers } from "next/headers";
import { auth } from "@/lib/auth";
import { monetizationEnabled } from "@/lib/monetization";
import { getPrismaClient } from "@/server/prisma";
import { getServerStripeClient } from "@/server/stripe";
import { upsertSubscriptionState } from "@/server/stripe-subscription-sync";

// Undo a scheduled cancellation so the subscription renews as normal.
export async function POST() {
  if (!monetizationEnabled) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const user = await getPrismaClient().user.findUnique({
    where: { id: session.user.id },
    select: { stripe_subscription_id: true, subscription_provider: true },
  });

  if (!user?.stripe_subscription_id || user.subscription_provider === "apple") {
    return NextResponse.json({ error: "No Stripe subscription to restart" }, { status: 400 });
  }

  const stripe = getServerStripeClient();
  try {
    let subscription = await stripe.subscriptions.retrieve(user.stripe_subscription_id);
    if (subscription.status === "canceled") {
      return NextResponse.json(
        { error: "This subscription has already ended. Choose a plan to subscribe again." },
        { status: 409 }
      );
    }

    if (subscription.cancel_at_period_end) {
      subscription = await stripe.subscriptions.update(subscription.id, {
        cancel_at_period_end: false,
      });
    } else if (subscription.cancel_at) {
      subscription = await stripe.subscriptions.update(subscription.id, { cancel_at: "" });
    }

    // Sync immediately instead of waiting for the webhook.
    await upsertSubscriptionState(subscription);
    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("Failed to restart Stripe subscription", error);
    return NextResponse.json({ error: "Unable to restart subscription" }, { status: 500 });
  }
}
