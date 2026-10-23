import { NextResponse } from "next/server";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";

async function requireAdmin() {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return { error: NextResponse.json({ error: "Unauthorized" }, { status: 401 }) };
  }

  const isAdmin = Boolean((session.user as { is_admin?: boolean }).is_admin);
  if (!isAdmin) {
    return { error: NextResponse.json({ error: "Forbidden" }, { status: 403 }) };
  }

  return { session };
}

/**
 * Grant or remove grandfathered 1080p (`users.legacy_hd`): `{ legacy_hd: boolean }`.
 * It only takes effect while the user is on an active Pro plan; Creator is 1080p anyway.
 */
export async function PATCH(request: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const adminCheck = await requireAdmin();
    if (adminCheck.error) {
      return adminCheck.error;
    }

    const { id } = await params;
    const body = await request.json().catch(() => null);
    const legacyHd = (body as { legacy_hd?: unknown } | null)?.legacy_hd;
    if (typeof legacyHd !== "boolean") {
      return NextResponse.json({ error: "legacy_hd must be a boolean" }, { status: 400 });
    }

    const result = await getPrismaClient().user.updateMany({
      where: { id },
      data: { legacy_hd: legacyHd },
    });
    if (result.count === 0) {
      return NextResponse.json({ error: "User not found" }, { status: 404 });
    }

    console.info("Admin changed grandfathered 1080p", {
      adminId: adminCheck.session.user.id,
      userId: id,
      legacyHd,
    });
    return NextResponse.json({ user: { id, legacy_hd: legacyHd } });
  } catch (error) {
    console.error("Failed to update grandfathered 1080p:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
