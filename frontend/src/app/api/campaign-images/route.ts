import { headers } from "next/headers";
import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";
import { backendTraceHeaders, buildBackendUrl } from "@/server/backend-api";

/** Stores a campaign image for the next project; the backend returns its id. */
export async function POST(request: Request) {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const formData = await request.formData();
  const upstream = await fetch(buildBackendUrl("/campaign-images"), {
    method: "POST",
    headers: { ...buildBackendAuthHeaders(session.user.id), ...(await backendTraceHeaders()) },
    body: formData,
  });

  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: { "Content-Type": upstream.headers.get("content-type") || "application/json" },
  });
}
