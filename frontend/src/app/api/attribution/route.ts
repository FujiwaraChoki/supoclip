import { NextResponse } from "next/server";

import { sanitizeAttribution } from "@/lib/attribution";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";

/** Only accounts this new are attributed, so later campaign clicks can't relabel existing users. */
const ATTRIBUTION_SIGNUP_WINDOW_MS = 24 * 60 * 60 * 1000;

// POST /api/attribution - store the browser's first-touch attribution once per new user
export async function POST(request: Request) {
  try {
    const session = await getServerSession();
    if (!session?.user?.id) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const attribution = sanitizeAttribution(await request.json().catch(() => null));
    if (!attribution) {
      return NextResponse.json({ error: "Invalid attribution payload" }, { status: 400 });
    }

    const createdAt = new Date(session.user.createdAt);
    if (Number.isNaN(createdAt.getTime()) || Date.now() - createdAt.getTime() > ATTRIBUTION_SIGNUP_WINDOW_MS) {
      return NextResponse.json({ stored: false, reason: "existing_user" });
    }

    const { captured_at, ...fields } = attribution;
    const result = await getPrismaClient().userAcquisition.createMany({
      data: [{ user_id: session.user.id, ...fields, first_seen_at: new Date(captured_at) }],
      skipDuplicates: true,
    });

    return NextResponse.json({ stored: result.count > 0 });
  } catch (error) {
    console.error("Error storing attribution:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
