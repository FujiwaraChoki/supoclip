"use client";

import { useEffect } from "react";
import posthog from "posthog-js";

import { PageError } from "@/components/app/page-state";
import { isPostHogEnabled } from "@/lib/posthog";

export default function ErrorPage({ error }: { error: Error & { digest?: string } }) {
  useEffect(() => {
    if (isPostHogEnabled) posthog.captureException(error);
  }, [error]);

  // Reload rather than reset(): re-rendering in place over a DOM that broke the page (for example
  // after browser translation rewrote it) crashes again with a hooks mismatch.
  return <PageError message="We couldn't load this page. Please try again." retry={() => window.location.reload()} />;
}
