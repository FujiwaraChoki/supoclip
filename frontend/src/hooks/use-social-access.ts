"use client";

import { useEffect, useState } from "react";

// One request per page load, shared by the shell and the pages it wraps.
let pending: Promise<boolean> | null = null;

/** True only when this server explicitly grants social publishing. Missing or false stays hidden. */
export function isSocialPublishingEnabled(data: { enabled?: boolean } | null | undefined) {
  return data?.enabled === true;
}

function loadSocialAccess() {
  pending ??= fetch("/api/social/providers", { cache: "no-store" })
    .then(async (response) => {
      if (!response.ok) return false;
      const data = (await response.json()) as { enabled?: boolean };
      return isSocialPublishingEnabled(data);
    })
    .catch(() => false);
  return pending;
}

/** Whether social publishing is available to the signed-in user (null while loading). */
export function useSocialAccess(enabled = true) {
  const [access, setAccess] = useState<boolean | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void loadSocialAccess().then((value) => {
      if (!cancelled) setAccess(value);
    });
    return () => {
      cancelled = true;
    };
  }, [enabled]);
  return access;
}
