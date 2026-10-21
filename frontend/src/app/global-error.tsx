"use client";

import { useEffect } from "react";
import posthog from "posthog-js";

import "./globals.css";
import { isPostHogEnabled } from "@/lib/posthog";

// Replaces the root layout when it crashes, so it can't rely on the intl provider or app chrome.
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    if (isPostHogEnabled) posthog.captureException(error);
  }, [error]);

  return (
    <html lang="en">
      <body className="antialiased">
        <div role="alert" className="mx-auto max-w-xl space-y-4 px-4 py-12 text-center">
          <h1 className="text-2xl font-semibold">Something went wrong</h1>
          <p className="text-muted-foreground">We couldn&apos;t load SupoClip. Please try again.</p>
          <button
            type="button"
            onClick={reset}
            className="rounded-md border px-4 py-2 text-sm font-medium hover:bg-muted"
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
