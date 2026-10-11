import { NextResponse } from "next/server";
import { parseAnnouncement, requireAnnouncementAdmin } from "@/server/announcements";
import { getPrismaClient } from "@/server/prisma";

export async function PATCH(request: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const denied = await requireAnnouncementAdmin(request);
    if (denied) return denied;
    const parsed = parseAnnouncement(await request.json().catch(() => null));
    if ("error" in parsed) return NextResponse.json({ error: parsed.error }, { status: 400 });
    const { id } = await params;
    const { count } = await getPrismaClient().announcement.updateMany({ where: { id }, data: parsed.data });
    if (!count) return NextResponse.json({ error: "Announcement not found" }, { status: 404 });
    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Failed to update announcement:", error);
    return NextResponse.json({ error: "Unable to save announcement" }, { status: 500 });
  }
}
