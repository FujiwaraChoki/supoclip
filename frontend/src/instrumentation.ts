import type { Instrumentation } from "next";

function firstHeader(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

export const onRequestError: Instrumentation.onRequestError = async (error, request, context) => {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;

  const { distinctIdFromCookie, getPostHogServer } = await import("@/server/posthog");
  const posthog = getPostHogServer();
  if (!posthog) return;

  const traceId = firstHeader(request.headers["x-trace-id"]);
  try {
    await posthog.captureExceptionImmediate(error, distinctIdFromCookie(firstHeader(request.headers.cookie)), {
      platform: "web-server",
      path: request.path,
      method: request.method,
      route_path: context.routePath,
      route_type: context.routeType,
      router_kind: context.routerKind,
      ...(traceId ? { trace_id: traceId } : {}),
    });
  } catch (captureError) {
    console.error("Failed to report request error to PostHog", captureError);
  }
};
