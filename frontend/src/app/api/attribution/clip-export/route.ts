import { NextResponse } from "next/server";

import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";

// POST /api/attribution/clip-export - mark the user's first clip export, exactly once
export async function POST() {
  try {
    const session = await getServerSession();
    if (!session?.user?.id) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const prisma = getPrismaClient();
    const exportedAt = new Date();
    // Conditional update is atomic, so concurrent exports can't both count as first.
    const { count } = await prisma.userAcquisition.updateMany({
      where: { user_id: session.user.id, first_clip_exported_at: null },
      data: { first_clip_exported_at: exportedAt },
    });
    if (count === 0) {
      return NextResponse.json({ first_export: false });
    }

    const attribution = await prisma.userAcquisition.findUnique({
      where: { user_id: session.user.id },
      select: {
        utm_source: true,
        utm_medium: true,
        utm_campaign: true,
        utm_content: true,
        ref: true,
        referrer_host: true,
      },
    });
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
