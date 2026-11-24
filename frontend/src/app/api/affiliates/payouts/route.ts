import { NextResponse } from "next/server";
import Stripe from "stripe";

import { isPayoutCountry } from "@/lib/creator-commission";
import { prismaCommissionStore } from "@/server/affiliate-commissions";
import {
  connectPlatformAccountId,
  connectPlatformClient,
  ensureConnectAccount,
  onboardingLink,
  type ConnectAffiliate,
} from "@/server/affiliate-payouts";
import { getServerSession } from "@/server/session";

/**
 * Creator payout setup through Stripe Connect Express, in the country the creator gave when
 * applying. `{ action: "onboard" }` returns Stripe's onboarding page for their account
 * (created on first use); `{ action: "dashboard" }` returns their Express dashboard.
 */
export async function POST(request: Request) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const body = (await request.json().catch(() => ({}))) as { action?: unknown };
  const store = prismaCommissionStore();
  const [affiliate] = await store.query<ConnectAffiliate & { profile_url: string; email: string; country: string | null }>(
    `SELECT a.user_id, a.slug, a.status, a.stripe_connect_account_id, a.stripe_connect_platform_id,
            a.payout_country, a.payouts_enabled, a.profile_url, a.country, u.email
       FROM affiliates a JOIN users u ON u.id = a.user_id WHERE a.user_id = $1`,
    [session.user.id],
  );
  if (affiliate?.status !== "approved") {
    return NextResponse.json({ error: "Only approved creators can set up payouts." }, { status: 403 });
  }
  if (!isPayoutCountry(affiliate.country)) {
    return NextResponse.json({ error: "Payouts aren't available in your country yet." }, { status: 403 });
  }
  const country = affiliate.country;

  try {
    const client = await connectPlatformClient();
    const appUrl = process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3107";
    const connected =
      affiliate.stripe_connect_account_id && affiliate.stripe_connect_platform_id === connectPlatformAccountId()
        ? affiliate.stripe_connect_account_id
        : null;

    if (body.action === "dashboard") {
      if (!connected) return NextResponse.json({ error: "Set up payouts first." }, { status: 409 });
      const link = await client.accounts.createLoginLink(connected);
      return NextResponse.json({ url: link.url });
    }

    if (body.action !== "onboard") {
      return NextResponse.json({ error: "Unknown action" }, { status: 400 });
    }
    const accountId =
      connected ??
      (await ensureConnectAccount(
        affiliate,
        { country, email: affiliate.email, profileUrl: affiliate.profile_url },
        { store, client },
      ));
    return NextResponse.json({ url: await onboardingLink(client, accountId, appUrl) });
  } catch (error) {
    console.error("Creator payout setup failed:", error);
    if (error instanceof Stripe.errors.StripeInvalidRequestError) {
      return NextResponse.json(
        { error: "Payouts can't be set up right now. Please try again later.", code: "payouts_unavailable" },
        { status: 503 },
      );
    }
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
