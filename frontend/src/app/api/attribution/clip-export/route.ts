import { NextResponse } from "next/server";

import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";
import { isWithinSignupWindow, prismaSqlExecutor, recordFirstClipExport } from "@/server/user-acquisition";

// POST /api/attribution/clip-export - mark the user's first clip export, exactly once
export async function POST() {
  try {
    const session = await getServerSession();
    if (!session?.user?.id) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const exportedAt = new Date();
    // New accounts may export before their attribution has synced; older
    // accounts without a row predate tracking and are never counted.
    const attribution = await recordFirstClipExport(prismaSqlExecutor(getPrismaClient()), session.user.id, exportedAt, {
      allowInsert: isWithinSignupWindow(session.user.createdAt, exportedAt),
    });
    if (!attribution) {
      return NextResponse.json({ first_export: false });
    }

    const createdAt = new Date(session.user.createdAt).getTime();
    return NextResponse.json({
      first_export: true,
      attribution,
      hours_since_signup: Number.isNaN(createdAt) ? null : Math.max(0, Math.round((exportedAt.getTime() - createdAt) / 36e5)),
    });
  } catch (error) {
    console.error("Error recording clip export:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
