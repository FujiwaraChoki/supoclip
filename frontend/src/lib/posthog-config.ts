/**
 * PostHog stays fully disabled unless a project key is baked into the build, so
 * self-hosted installs never send data anywhere by default.
 */
export const POSTHOG_KEY = process.env.NEXT_PUBLIC_POSTHOG_KEY?.trim() || "";
export const POSTHOG_HOST = (process.env.NEXT_PUBLIC_POSTHOG_HOST?.trim() || "https://eu.i.posthog.com").replace(/\/$/, "");
export const isPostHogEnabled = POSTHOG_KEY !== "";

/** PostHog Cloud serves the web UI from `eu.posthog.com` for the `eu.i.posthog.com` ingest host. */
export function posthogUiHost(host = POSTHOG_HOST) {
  return host.replace(/^https:\/\/(us|eu)\.i\.posthog\.com$/, "https://$1.posthog.com");
}
