"use client";

import { useQuery } from "@tanstack/react-query";

import { agentKeys, fetchAgentStatus, type AgentStatus } from "@/lib/agent";

/**
 * Whether the agent home is offered to the signed-in person, and what they chose.
 * `undefined` while loading; a failed request counts as unavailable so home falls back to the form.
 */
export function useAgentStatus(enabled = true): AgentStatus | null | undefined {
  const { data, isError } = useQuery({
    queryKey: agentKeys.status,
    queryFn: fetchAgentStatus,
    enabled,
    staleTime: 60_000,
    retry: 1,
  });
  if (!enabled) return null;
  if (isError) return null;
  return data;
}
