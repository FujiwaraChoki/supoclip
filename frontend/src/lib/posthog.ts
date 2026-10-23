import posthog from "posthog-js";

import { isPostHogEnabled } from "@/lib/posthog-config";

export { isPostHogEnabled };

export function resetAnalytics() {
  if (isPostHogEnabled) posthog.reset();
}

export function captureApiFailure(details: { method: string; path: string; status: number; traceId: string }) {
  if (!isPostHogEnabled) return;
  posthog.captureException(new Error(`${details.method} ${details.path} failed with ${details.status}`), {
    trace_id: details.traceId,
    status: details.status,
    path: details.path,
    method: details.method,
  });
}
