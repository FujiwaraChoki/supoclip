import { NextResponse } from "next/server";
import { getPrismaClient } from "@/server/prisma";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const announcements = await getPrismaClient().announcement.findMany({
      where: { isPublished: true, publishedAt: { lte: new Date() } },
      orderBy: [{ publishedAt: "desc" }, { id: "desc" }],
      select: { id: true, title: true, description: true, href: true, action: true, publishedAt: true },
    });
    return NextResponse.json({ announcements }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    console.error("Failed to load announcements:", error);
    return NextResponse.json({ error: "Unable to load announcements" }, { status: 500 });
  }
}
