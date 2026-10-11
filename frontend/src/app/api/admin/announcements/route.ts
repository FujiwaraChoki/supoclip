import { NextResponse } from "next/server";
import { parseAnnouncement, requireAnnouncementAdmin } from "@/server/announcements";
import { getPrismaClient } from "@/server/prisma";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const denied = await requireAnnouncementAdmin();
    if (denied) return denied;
    const announcements = await getPrismaClient().announcement.findMany({ orderBy: [{ publishedAt: "desc" }, { id: "desc" }] });
    return NextResponse.json({ announcements }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    console.error("Failed to load managed announcements:", error);
    return NextResponse.json({ error: "Unable to load announcements" }, { status: 500 });
  }
}

export async function POST(request: Request) {
  try {
    const denied = await requireAnnouncementAdmin(request);
    if (denied) return denied;
    const parsed = parseAnnouncement(await request.json().catch(() => null));
    if ("error" in parsed) return NextResponse.json({ error: parsed.error }, { status: 400 });
    const announcement = await getPrismaClient().announcement.create({ data: parsed.data });
    return NextResponse.json({ announcement }, { status: 201 });
  } catch (error) {
    console.error("Failed to create announcement:", error);
    return NextResponse.json({ error: "Unable to save announcement" }, { status: 500 });
  }
}
