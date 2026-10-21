"use client";

import { useEffect } from "react";
import posthog from "posthog-js";

import { PageError } from "@/components/app/page-state";
import { isPostHogEnabled } from "@/lib/posthog";

export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    if (isPostHogEnabled) posthog.captureException(error);
  }, [error]);

  return <PageError message="We couldn't load this page. Please try again." retry={reset} />;
}
