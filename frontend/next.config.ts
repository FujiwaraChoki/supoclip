import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";
import { withPostHogConfig } from "@posthog/nextjs-config";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

const isPostHogEnabled = Boolean(process.env.NEXT_PUBLIC_POSTHOG_KEY?.trim());
const posthogHost = (process.env.NEXT_PUBLIC_POSTHOG_HOST?.trim() || "https://eu.i.posthog.com").replace(/\/$/, "");
// PostHog Cloud serves the SDK bundles from a separate assets host.
const posthogAssetsHost = posthogHost.replace(/^https:\/\/(us|eu)\.i\.posthog\.com$/, "https://$1-assets.i.posthog.com");

// Same-origin reverse proxy so ad blockers don't drop analytics, replays, and error reports.
const posthogRewrites = isPostHogEnabled
  ? [
      { source: "/ingest/static/:path*", destination: `${posthogAssetsHost}/static/:path*` },
      { source: "/ingest/array/:path*", destination: `${posthogAssetsHost}/array/:path*` },
      { source: "/ingest/:path*", destination: `${posthogHost}/:path*` },
    ]
  : [];

// Price copy reads the public name. When it is unset, reuse the server flag so
// a single build-time value turns checkout and the "+ applicable tax" copy on together.
const managedPaymentsFlag =
  process.env.NEXT_PUBLIC_STRIPE_MANAGED_PAYMENTS_ENABLED?.trim() ||
  process.env.STRIPE_MANAGED_PAYMENTS_ENABLED?.trim() ||
  "";

const nextConfig: NextConfig = {
  env: {
    NEXT_PUBLIC_STRIPE_MANAGED_PAYMENTS_ENABLED: managedPaymentsFlag,
  },
  distDir: process.env.NEXT_BUILD_DIR || ".next",
  output: 'standalone',
  // PostHog's API paths end in a slash (e.g. /e/); don't redirect them.
  skipTrailingSlashRedirect: isPostHogEnabled,
  typescript: {
    ignoreBuildErrors: false,
  },
  async rewrites() {
    return [
      {
        source: "/js/script.js",
        destination: "https://datafa.st/js/script.js",
      },
      {
        source: "/api/events",
        destination: "https://datafa.st/api/events",
      },
      ...posthogRewrites,
    ];
  },
};

const intlConfig = withNextIntl(nextConfig);

// Source maps upload only when a build has PostHog credentials; other builds are unchanged.
const posthogPersonalApiKey = process.env.POSTHOG_PERSONAL_API_KEY?.trim();
const posthogProjectId = process.env.POSTHOG_PROJECT_ID?.trim();

export default posthogPersonalApiKey && posthogProjectId
  ? withPostHogConfig(intlConfig, {
      personalApiKey: posthogPersonalApiKey,
      projectId: posthogProjectId,
      host: posthogHost.replace(/^https:\/\/(us|eu)\.i\.posthog\.com$/, "https://$1.posthog.com"),
      sourcemaps: {
        enabled: true,
        releaseName: "supoclip-web",
        releaseVersion: process.env.POSTHOG_RELEASE_VERSION?.trim() || undefined,
        deleteAfterUpload: true,
      },
    })
  : intlConfig;
