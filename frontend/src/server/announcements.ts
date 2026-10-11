import { NextResponse } from "next/server";
import { getAuthTrustedOrigins } from "@/lib/auth-origins";
import { getServerSession } from "@/server/session";

export async function requireAnnouncementAdmin(request?: Request) {
  const session = await getServerSession();
  if (!session?.user?.id) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  if (!(session.user as { is_admin?: boolean }).is_admin) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }
  const origin = request?.headers.get("origin");
  const trustedOrigins = getAuthTrustedOrigins({
    NEXT_PUBLIC_APP_URL: process.env.NEXT_PUBLIC_APP_URL,
    BETTER_AUTH_URL: process.env.BETTER_AUTH_URL,
    BETTER_AUTH_TRUSTED_ORIGINS: process.env.BETTER_AUTH_TRUSTED_ORIGINS,
    NEXT_PUBLIC_SELF_HOST: process.env.NEXT_PUBLIC_SELF_HOST,
  });
  // Standalone Next.js sees its internal host behind the production proxy.
  if (request && origin && origin !== new URL(request.url).origin && !trustedOrigins.includes(origin)) {
    return NextResponse.json({ error: "Cross-origin writes are not allowed" }, { status: 403 });
  }
  return null;
}

export function parseAnnouncement(body: unknown) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return { error: "Invalid announcement" } as const;
  const input = body as Record<string, unknown>;
  const limits = { title: 120, description: 2000, href: 500, action: 80 } as const;
  const text = {} as Record<keyof typeof limits, string>;
  for (const field of Object.keys(limits) as (keyof typeof limits)[]) {
    const value = input[field];
    if (typeof value !== "string" || !value.trim() || value.trim().length > limits[field]) {
      return { error: `${field} must be between 1 and ${limits[field]} characters` } as const;
    }
    text[field] = value.trim();
  }
  if (!/^\/(?!\/)[^\s\\]*$/.test(text.href)) {
    return { error: "Destination must be a SupoClip path, such as /affiliate" } as const;
  }
  if (typeof input.isPublished !== "boolean") return { error: "Choose draft or published" } as const;
  const publishedAt = typeof input.publishedAt === "string" ? new Date(input.publishedAt) : new Date(NaN);
  if (Number.isNaN(publishedAt.getTime())) return { error: "Choose a valid publication date" } as const;
  return { data: { ...text, isPublished: input.isPublished, publishedAt } } as const;
}
