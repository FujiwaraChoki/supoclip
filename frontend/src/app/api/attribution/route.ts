import { NextResponse } from "next/server";

import { sanitizeAttribution } from "@/lib/attribution";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";
import {
  getSignupAttribution,
  isWithinSignupWindow,
  prismaSqlExecutor,
  saveSignupAttribution,
} from "@/server/user-acquisition";

/**
 * Whether the browser may treat this answer as final. An empty answer for a
 * new account isn't: another device may still sync its first visit.
 */
function isConfirmed(attribution: unknown, createdAt: Date | string) {
  return attribution !== null || !isWithinSignupWindow(createdAt, new Date());
}

// GET /api/attribution - the signed-in account's frozen attribution
export async function GET() {
  try {
    const session = await getServerSession();
    if (!session?.user?.id) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const attribution = await getSignupAttribution(prismaSqlExecutor(getPrismaClient()), session.user.id);
    return NextResponse.json({ attribution, confirmed: isConfirmed(attribution, session.user.createdAt) });
  } catch (error) {
    console.error("Error reading attribution:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}

// POST /api/attribution - store this browser's anonymous first-touch attribution once per new user
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

    const sql = prismaSqlExecutor(getPrismaClient());
    const isNewAccount = isWithinSignupWindow(session.user.createdAt, new Date());
    const stored = isNewAccount && (await saveSignupAttribution(sql, session.user.id, attribution));
    // Always answer with what the account has stored, so the browser keeps the server's value.
    const storedAttribution = await getSignupAttribution(sql, session.user.id);
    return NextResponse.json({
      stored,
      ...(isNewAccount ? {} : { reason: "existing_user" }),
      attribution: storedAttribution,
      confirmed: isConfirmed(storedAttribution, session.user.createdAt),
    });
  } catch (error) {
    console.error("Error storing attribution:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
