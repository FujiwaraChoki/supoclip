import { NextResponse } from "next/server";

import { requireAnnouncementAdmin as requireAdmin } from "@/server/announcements";
import { createTextProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

/** Backend prefixes the admin feedback proxies may reach (backend/src/api/routes/admin_feedback.py). */
export type AdminFeedbackPrefix = "/admin/feedback" | "/admin/feedback-webhooks";

const SEGMENT = /^[A-Za-z0-9_-]{1,64}$/;

/**
 * Forward an admin's request to the backend's feedback inbox API.
 *
 * Admin-only (401/403 before anything reaches the backend, which checks again), and
 * writes must come from our own origin. Path segments are restricted to ids and
 * action names, so a request can't walk out of its prefix.
 */
export async function proxyAdminFeedback(
  request: Request,
  prefix: AdminFeedbackPrefix,
  params: Promise<{ path?: string[] }>,
) {
  const denied = await requireAdmin(request.method === "GET" ? undefined : request);
  if (denied) return denied;
  const session = await getServerSession();
  const userId = session?.user?.id;
  if (!userId) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const segments = (await params).path ?? [];
  if (segments.length > 3 || !segments.every((segment) => SEGMENT.test(segment))) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }
  const search = request.method === "GET" ? new URL(request.url).search : "";
  const path = [prefix, ...segments].join("/") + search;
  const hasBody = !["GET", "DELETE"].includes(request.method);
  const upstream = await fetchBackend(path, {
    method: request.method,
    userId,
    cache: "no-store",
    ...(hasBody ? { body: await request.text(), extraHeaders: { "Content-Type": "application/json" } } : {}),
  });
  return createTextProxyResponse(upstream);
}
