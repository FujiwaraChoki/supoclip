"use client";

import { useQuery } from "@tanstack/react-query";
import { isSocialPublishingEnabled } from "@/hooks/use-social-access";
import type { SocialConnection, SocialProviderStatus } from "@/lib/social";

export interface SocialSettings {
  /** Whether this server grants social publishing to the user (`enabled === true` only). */
  enabled: boolean;
  providers: SocialProviderStatus[];
  connections: SocialConnection[];
}

export const socialSettingsKeys = {
  all: ["social-settings"] as const,
  detail: (userId: string) => [...socialSettingsKeys.all, userId] as const,
};

/**
 * Providers decide whether the page is available at all, so a failed providers request
 * throws (with `loadError` as its message). A failed connections request shows no accounts.
 */
async function fetchSocialSettings(loadError: string): Promise<SocialSettings> {
  const [providersResponse, connectionsResponse] = await Promise.all([
    fetch("/api/social/providers", { cache: "no-store" }),
    fetch("/api/social/connections", { cache: "no-store" }),
  ]);
  if (!providersResponse.ok) throw new Error(loadError);
  const providersData = (await providersResponse.json()) as {
    enabled?: boolean;
    providers: SocialProviderStatus[];
  };
  const connectionsData = connectionsResponse.ok
    ? ((await connectionsResponse.json()) as { connections: SocialConnection[] })
    : { connections: [] };
  return {
    enabled: isSocialPublishingEnabled(providersData),
    providers: providersData.providers ?? [],
    connections: connectionsData.connections ?? [],
  };
}

export function useSocialSettings(userId: string | undefined, loadError: string) {
  return useQuery({
    queryKey: socialSettingsKeys.detail(userId ?? ""),
    queryFn: () => fetchSocialSettings(loadError),
    enabled: Boolean(userId),
  });
}
