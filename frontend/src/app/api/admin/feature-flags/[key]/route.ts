import { NextResponse } from "next/server";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";

/** The columns an admin can switch. */
const FEATURE_FLAG_FIELDS = ["web_enabled", "ios_enabled", "admins_bypass"] as const;
type FeatureFlagField = (typeof FEATURE_FLAG_FIELDS)[number];

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
 * Switch a feature flag: any of `{ web_enabled, ios_enabled, admins_bypass }` as booleans.
 * The backend caches flags for a few seconds (backend/src/feature_flags.py), so a change
 * reaches every request within about 15 seconds.
 */
export async function PATCH(request: Request, { params }: { params: Promise<{ key: string }> }) {
  try {
    const adminCheck = await requireAdmin();
    if (adminCheck.error) {
      return adminCheck.error;
    }

    const { key } = await params;
    const body = await request.json().catch(() => null);
    if (!body || typeof body !== "object" || Array.isArray(body)) {
      return NextResponse.json({ error: "Send web_enabled, ios_enabled or admins_bypass" }, { status: 400 });
    }
    const data: Partial<Record<FeatureFlagField, boolean>> = {};
    for (const [field, value] of Object.entries(body as Record<string, unknown>)) {
      if (!(FEATURE_FLAG_FIELDS as readonly string[]).includes(field) || typeof value !== "boolean") {
        return NextResponse.json({ error: `${field} is not a switchable boolean` }, { status: 400 });
      }
      data[field as FeatureFlagField] = value;
    }
    if (Object.keys(data).length === 0) {
      return NextResponse.json({ error: "Send web_enabled, ios_enabled or admins_bypass" }, { status: 400 });
    }

    const result = await getPrismaClient().featureFlag.updateMany({
      where: { key },
      data: { ...data, updated_by: adminCheck.session.user.id },
    });
    if (result.count === 0) {
      return NextResponse.json({ error: "Feature flag not found" }, { status: 404 });
    }

    console.info("Admin changed a feature flag", { adminId: adminCheck.session.user.id, key, ...data });
    return NextResponse.json({ flag: { key, ...data } });
  } catch (error) {
    console.error("Failed to update feature flag:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
