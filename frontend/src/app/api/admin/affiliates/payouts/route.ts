import { NextResponse } from "next/server";

import { MINIMUM_PAYOUT_MINOR } from "@/lib/creator-commission";
import { prismaCommissionStore } from "@/server/affiliate-commissions";
import { connectPlatformClient, creatorBalances, runCreatorPayouts } from "@/server/affiliate-payouts";
import { getServerSession } from "@/server/session";

async function requireAdmin() {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return { error: NextResponse.json({ error: "Unauthorized" }, { status: 401 }) };
  }
  if (!(session.user as { is_admin?: boolean }).is_admin) {
    return { error: NextResponse.json({ error: "Forbidden" }, { status: 403 }) };
  }
  return { session };
}

/** Every approved creator's unpaid commission, per currency. */
export async function GET() {
  const admin = await requireAdmin();
  if (admin.error) return admin.error;
  try {
    const balances = await creatorBalances(prismaCommissionStore());
    return NextResponse.json({ balances, minimum_payout: MINIMUM_PAYOUT_MINOR });
  } catch (error) {
    console.error("Failed to load creator balances:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}

/** Pay every creator whose available commission reaches the minimum. */
export async function POST() {
  const admin = await requireAdmin();
  if (admin.error) return admin.error;
  try {
    const store = prismaCommissionStore();
    const results = await runCreatorPayouts(store, await connectPlatformClient(), {
      adminId: admin.session.user.id,
    });
    return NextResponse.json({ results, balances: await creatorBalances(store) });
  } catch (error) {
    console.error("Creator payout run failed:", error);
    return NextResponse.json({ error: "Payout run failed. Nothing was paid twice; try again." }, { status: 500 });
  }
}
