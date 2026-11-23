"use client";

import { useQuery } from "@tanstack/react-query";

/** Key metadata only. The plaintext `sk_...` is never part of this list or any cache. */
export interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  created_at: string | null;
  last_used_at: string | null;
  revoked: boolean;
}

export const apiKeyKeys = {
  all: ["api-keys"] as const,
  list: (userId: string) => [...apiKeyKeys.all, userId] as const,
};

async function fetchApiKeys(): Promise<ApiKey[]> {
  const response = await fetch("/api/api-keys", { cache: "no-store" });
  if (!response.ok) throw new Error(`Failed to load API keys: ${response.status}`);
  const data = (await response.json()) as { api_keys?: ApiKey[] };
  return data.api_keys || [];
}

/** The signed-in user's API keys (metadata only). */
export function useApiKeys(userId: string | undefined) {
  return useQuery({
    queryKey: apiKeyKeys.list(userId ?? ""),
    queryFn: fetchApiKeys,
    enabled: Boolean(userId),
  });
}
