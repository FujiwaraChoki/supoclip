import { NextResponse } from "next/server";

import { isPayoutCountry } from "@/lib/creator-commission";
import { OTHER_COUNTRY } from "@/server/affiliates";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";

/**
 * Where a creator lives, for creators who applied before the form asked. Set once: it
 * decides whether they earn commission and where Stripe pays it, so changes go through us.
 */
export async function POST(request: Request) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const body = (await request.json().catch(() => ({}))) as { country?: unknown };
  if (!isPayoutCountry(body.country) && body.country !== OTHER_COUNTRY) {
    return NextResponse.json({ error: "Pick where you live." }, { status: 400 });
  }
  const { count } = await getPrismaClient().affiliate.updateMany({
    where: { user_id: session.user.id, country: null, status: { in: ["pending", "approved"] } },
    data: { country: body.country as string },
  });
  if (count === 0) {
    return NextResponse.json({ error: "Your country is already set. Contact us to change it." }, { status: 409 });
  }
  return NextResponse.json({ country: body.country });
}
