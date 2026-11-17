import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import {
  BillingAccountHoldError,
  checkoutStripeAccountId,
  runWithStripeAccount,
  stripeAccountsEnabled,
  verifiedStripeClient,
} from "@/lib/stripe-accounts";
import { findCreatorOffer } from "@/server/affiliates";
import { getPrismaClient } from "@/server/prisma";
import { billingAccountRoute } from "@/server/stripe-account-routing";

/** Public: whether `?code=maya` is an approved creator's (or a house) code. Codes are shared publicly by design. */
export async function GET(request: Request) {
  try {
    const code = new URL(request.url).searchParams.get("code") ?? "";
    const lookup = async () => {
      const offer = await findCreatorOffer(getPrismaClient(), code);
      if (!offer) {
        return NextResponse.json({ error: "Unknown creator code" }, { status: 404 });
      }
      return NextResponse.json({ code: offer.code });
    };
    if (!stripeAccountsEnabled()) return await lookup();

    // Validate against the same account Checkout will use. The route also
    // serves anonymous referral links, before a user has a billing record.
    const session = await auth.api.getSession({ headers: request.headers });
    if (session?.user?.id) {
      return await billingAccountRoute(session.user.id, "checkout", lookup);
    }
    const accountId = checkoutStripeAccountId();
    const client = await verifiedStripeClient(accountId);
    return await runWithStripeAccount({ accountId, client }, lookup);
  } catch (error) {
    if (error instanceof BillingAccountHoldError) {
      return NextResponse.json(
        { error: error.message, code: "billing_transfer_pending" },
        { status: 409 },
      );
    }
    console.error("Failed to look up creator code:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
