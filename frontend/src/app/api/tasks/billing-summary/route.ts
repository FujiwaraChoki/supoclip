import { headers } from "next/headers";
import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";
import { backendTraceHeaders } from "@/server/backend-api";
import { restorePastDueEntitlement } from "@/server/stripe-subscription-sync";

export async function GET() {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const apiUrl =
    process.env.BACKEND_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000";
  const loadSummary = async () => {
    const upstream = await fetch(`${apiUrl}/tasks/billing/summary`, {
      method: "GET",
      headers: { ...buildBackendAuthHeaders(session.user.id), ...(await backendTraceHeaders()) },
      cache: "no-store",
    });
    return { upstream, responseText: await upstream.text() };
  };

  let { upstream, responseText } = await loadSummary();
  if (upstream.ok && storedPastDuePlanIsFree(responseText)) {
    try {
      if (await restorePastDueEntitlement(session.user.id)) {
        ({ upstream, responseText } = await loadSummary());
      }
    } catch (error) {
      console.error("Failed to restore past-due subscription", error);
    }
  }

  const traceId = upstream.headers.get("x-trace-id");
  return new NextResponse(responseText, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") || "application/json",
      ...(traceId ? { "x-trace-id": traceId } : {}),
    },
  });
}

function storedPastDuePlanIsFree(responseText: string): boolean {
  try {
    const summary = JSON.parse(responseText) as {
      plan?: string;
      subscription_status?: string;
      subscription_provider?: string | null;
    };
    return (
      summary.plan === "free" &&
      summary.subscription_status === "past_due" &&
      summary.subscription_provider === "stripe"
    );
  } catch {
    return false;
  }
}
