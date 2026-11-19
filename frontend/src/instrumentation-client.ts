import posthog from "posthog-js";

import { installDomTranslationGuard } from "@/lib/dom-translation-guard";
import { POSTHOG_KEY, isPostHogEnabled, posthogUiHost } from "@/lib/posthog-config";
import { installTraceFetch } from "@/lib/trace-fetch";

installDomTranslationGuard();
installTraceFetch();

if (isPostHogEnabled) {
  posthog.init(POSTHOG_KEY, {
    api_host: "/ingest",
    ui_host: posthogUiHost(),
    defaults: "2026-08-30",
    person_profiles: "identified_only",
    capture_exceptions: true,
    session_recording: {
      maskAllInputs: true,
    },
  });
  posthog.register({ platform: "web" });
}
