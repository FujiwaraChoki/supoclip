import { NextResponse } from "next/server";
import {
  applyAvailableAt,
  isSlugAvailable,
  parseApplication,
  sendAffiliateEmail,
} from "@/server/affiliates";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";

const APPLICATION_SELECT = {
  slug: true,
  status: true,
  platform: true,
  profile_url: true,
  audience_size: true,
  video_url: true,
  promotion_plan: true,
  decline_reason: true,
  app_store_code_added_at: true,
  reviewed_at: true,
  created_at: true,
} as const;

/** The signed-in user's application (if any) and when they may apply. */
export async function GET() {
  try {
    const session = await getServerSession();
    if (!session?.user?.id) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const application = await getPrismaClient().affiliate.findUnique({
      where: { user_id: session.user.id },
      select: APPLICATION_SELECT,
    });
    return NextResponse.json({
      application,
      can_apply_at: applyAvailableAt(application)?.toISOString() ?? null,
    });
  } catch (error) {
    console.error("Failed to load affiliate application:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}

export async function POST(request: Request) {
  try {
    const session = await getServerSession();
    if (!session?.user?.id) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const parsed = parseApplication(await request.json().catch(() => null));
    if (!parsed.ok) {
      return NextResponse.json({ error: parsed.error }, { status: 400 });
    }
    const { application } = parsed;
    const userId = session.user.id;
    const prisma = getPrismaClient();

    const existing = await prisma.affiliate.findUnique({
      where: { user_id: userId },
      select: { status: true, reviewed_at: true },
    });
    const availableAt = applyAvailableAt(existing);
    if (!availableAt || availableAt.getTime() > Date.now()) {
      return NextResponse.json(
        { error: existing?.status === "declined" ? "You can apply again 30 days after a decision." : "You've already applied." },
        { status: 409 },
      );
    }
    if (!(await isSlugAvailable(prisma, application.slug, userId))) {
      return NextResponse.json({ error: "That code is already taken." }, { status: 409 });
    }

    const fields = {
      ...application,
      status: "pending",
      decline_reason: null,
      reviewed_at: null,
      reviewed_by: null,
      terms_accepted_at: new Date(),
    };
    let saved;
    try {
      saved = await prisma.affiliate.upsert({
        where: { user_id: userId },
        create: { user_id: userId, ...fields },
        update: fields,
        select: APPLICATION_SELECT,
      });
    } catch (error) {
      // Another applicant claimed the slug between the check and the write.
      if ((error as { code?: string }).code === "P2002") {
        return NextResponse.json({ error: "That code is already taken." }, { status: 409 });
      }
      throw error;
    }

    sendAffiliateEmail(userId, { event: "applied", ...application });
    return NextResponse.json({ application: saved, can_apply_at: null }, { status: 201 });
  } catch (error) {
    console.error("Failed to submit affiliate application:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
