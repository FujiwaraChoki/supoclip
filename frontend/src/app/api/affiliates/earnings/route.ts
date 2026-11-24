import { NextResponse } from "next/server";

import {
  COMMISSION_HOLD_DAYS,
  COMMISSION_MONTHS,
  COMMISSION_PERCENT,
  MINIMUM_PAYOUT_MINOR,
  isPayoutCountry,
} from "@/lib/creator-commission";
import {
  earningsTotals,
  prismaCommissionStore,
  recentCommissions,
  referralCounts,
} from "@/server/affiliate-commissions";
import {
  connectPlatformClient,
  recentPayouts,
  refreshConnectStatus,
  type ConnectAffiliate,
  type ConnectStatus,
} from "@/server/affiliate-payouts";
import { getServerSession } from "@/server/session";

/** The signed-in creator's commission, referrals, payouts and payout setup. */
export async function GET() {
  try {
    const session = await getServerSession();
    if (!session?.user?.id) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const userId = session.user.id;
    const store = prismaCommissionStore();
    const [affiliate] = await store.query<ConnectAffiliate & { country: string | null }>(
      `SELECT user_id, slug, status, stripe_connect_account_id, stripe_connect_platform_id, payout_country,
              payouts_enabled, country
         FROM affiliates WHERE user_id = $1`,
      [userId],
    );
    if (affiliate?.status !== "approved") {
      return NextResponse.json({ error: "Not in the creator program" }, { status: 404 });
    }
    if (!isPayoutCountry(affiliate.country)) {
      return NextResponse.json({ error: "Commission isn't available in your country" }, { status: 404 });
    }

    let payouts: { status: ConnectStatus | "unavailable"; country: string | null; details_submitted: boolean } = {
      status: affiliate.stripe_connect_account_id ? (affiliate.payouts_enabled ? "enabled" : "incomplete") : "not_started",
      country: affiliate.country,
      details_submitted: false,
    };
    if (affiliate.stripe_connect_account_id) {
      try {
        const refreshed = await refreshConnectStatus(affiliate, { store, client: await connectPlatformClient() });
        payouts = { status: refreshed.status, country: affiliate.country, details_submitted: refreshed.detailsSubmitted };
      } catch (error) {
        console.error("Failed to refresh creator payout account:", error);
        payouts = { ...payouts, status: "unavailable" };
      }
    }

    const now = new Date();
    const [totals, referrals, activity, payoutHistory] = await Promise.all([
      earningsTotals(store, userId, now),
      referralCounts(store, userId),
      recentCommissions(store, userId),
      recentPayouts(store, userId),
    ]);
    return NextResponse.json({
      terms: {
        percent: COMMISSION_PERCENT,
        months: COMMISSION_MONTHS,
        hold_days: COMMISSION_HOLD_DAYS,
        minimum_payout: MINIMUM_PAYOUT_MINOR,
      },
      totals,
      referrals,
      activity,
      payout_history: payoutHistory,
      payouts,
    });
  } catch (error) {
    console.error("Failed to load creator earnings:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
