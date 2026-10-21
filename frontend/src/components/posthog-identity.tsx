"use client";

import { useEffect, useRef } from "react";
import posthog from "posthog-js";

import { useSession } from "@/lib/auth-client";
import { isPostHogEnabled } from "@/lib/posthog";

export function PostHogIdentity() {
  const { data: session, isPending } = useSession();
  const identifiedIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!isPostHogEnabled || isPending) return;
    const user = session?.user;

    if (!user?.id) {
      // The session ended in this tab (expiry, sign-out elsewhere): stop attributing events.
      if (identifiedIdRef.current) {
        posthog.reset();
        identifiedIdRef.current = null;
      }
      return;
    }

    if (identifiedIdRef.current === user.id) return;
    posthog.identify(user.id, {
      email: user.email,
      ...(user.name ? { name: user.name } : {}),
    });
    identifiedIdRef.current = user.id;
  }, [isPending, session?.user]);

  return null;
}
