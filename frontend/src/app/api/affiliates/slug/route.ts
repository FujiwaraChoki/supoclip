import { NextResponse } from "next/server";
import { isSlugAvailable, validateSlug } from "@/server/affiliates";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";

/** Live availability check for the application form: `?slug=maya`. */
export async function GET(request: Request) {
  try {
    const session = await getServerSession();
    if (!session?.user?.id) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const check = validateSlug(new URL(request.url).searchParams.get("slug"));
    if (!check.ok) {
      return NextResponse.json({ available: false, reason: check.error });
    }
    const available = await isSlugAvailable(getPrismaClient(), check.slug, session.user.id);
    return NextResponse.json({
      slug: check.slug,
      available,
      ...(available ? {} : { reason: "That code is already taken." }),
    });
  } catch (error) {
    console.error("Failed to check affiliate slug:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
