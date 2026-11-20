import { NextResponse } from "next/server";
import { headers } from "next/headers";
import { auth } from "@/lib/auth";
import { monetizationEnabled } from "@/lib/monetization";
import { stripeAccountsEnabled } from "@/lib/stripe-accounts";
import { billingAccountRoute } from "@/server/stripe-account-routing";
import { getPrismaClient } from "@/server/prisma";
import { getServerStripeClient } from "@/server/stripe";
import { syncSubscriptionUpdate } from "@/server/stripe-subscription-sync";

/** Explicit cancellation remains available while a staged import holds plan changes. */
export async function POST(): Promise<Response> {
  if (!monetizationEnabled || !stripeAccountsEnabled())
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id)
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  return billingAccountRoute(session.user.id, "cancel", async () => {
    const user = await getPrismaClient().user.findUniqueOrThrow({
      where: { id: session.user.id },
    });
    if (!user.stripe_subscription_id)
      return NextResponse.json(
        { error: "No Stripe subscription" },
        { status: 400 },
      );
    const stripe = getServerStripeClient();
    const current = await stripe.subscriptions.retrieve(
      user.stripe_subscription_id,
    );
    if (current.status !== "canceled") {
      const updated = await stripe.subscriptions.update(current.id, {
        cancel_at_period_end: true,
      });
      await syncSubscriptionUpdate(updated);
    }
    return NextResponse.json({ ok: true });
  });
}
