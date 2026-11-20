import posthog from "posthog-js";

import { installDomTranslationGuard, reportSkippedDomOperations } from "@/lib/dom-translation-guard";
import { POSTHOG_KEY, isPostHogEnabled, posthogUiHost } from "@/lib/posthog-config";
import { installTraceFetch } from "@/lib/trace-fetch";

installDomTranslationGuard(
  isPostHogEnabled
    ? reportSkippedDomOperations((error, properties) => posthog.captureException(error, properties))
    : undefined,
);
installTraceFetch();

if (isPostHogEnabled) {
  posthog.init(POSTHOG_KEY, {
    api_host: "/ingest",
    ui_host: posthogUiHost(),
    defaults: "2026-08-30",
    person_profiles: "identified_only",
    capture_exceptions: true,
    // Autocaptured clicks and web vitals were most of our event volume. Pageviews,
    // custom events, exceptions and session replay's network timing stay on.
    autocapture: false,
    capture_performance: { web_vitals: false },
    session_recording: {
      maskAllInputs: true,
    },
  });
  posthog.register({ platform: "web" });
}
