"use client";

import { useQuery } from "@tanstack/react-query";
import { useSession } from "@/lib/auth-client";

/** True only when this server explicitly grants social publishing. Missing or false stays hidden. */
export function isSocialPublishingEnabled(data: { enabled?: boolean } | null | undefined) {
  return data?.enabled === true;
}

async function loadSocialAccess(): Promise<boolean> {
  try {
    const response = await fetch("/api/social/providers", { cache: "no-store" });
    if (!response.ok) return false;
    return isSocialPublishingEnabled((await response.json()) as { enabled?: boolean });
  } catch {
    return false;
  }
}

/** Whether social publishing is available to the signed-in user (null while loading). */
export function useSocialAccess(enabled = true) {
  const { data: session } = useSession();
  const { data } = useQuery({
    queryKey: ["social-access", session?.user?.id ?? "anonymous"],
    queryFn: loadSocialAccess,
    enabled,
    // Asked once per page load, shared by the shell and the pages it wraps.
    staleTime: Infinity,
  });
  return enabled ? data ?? null : null;
}
