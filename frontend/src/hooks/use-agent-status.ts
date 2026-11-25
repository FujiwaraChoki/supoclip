"use client";

import { useQuery } from "@tanstack/react-query";

import { agentKeys, fetchAgentStatus, type AgentStatus } from "@/lib/agent";

/**
 * Whether the agent home is offered to the signed-in person, and what they chose.
 * `undefined` while loading; a failed request counts as unavailable so home falls back to the form.
 *
 * A failure is cached as `null` rather than left as a query error: react-query puts an errored
 * query without data back to `pending` whenever another component mounts it, which would flip
 * home back to its skeleton, unmount the form, and start over.
 */
export function useAgentStatus(enabled = true): AgentStatus | null | undefined {
  const { data } = useQuery({
    queryKey: agentKeys.status,
    queryFn: () => fetchAgentStatus().catch(() => null),
    enabled,
    staleTime: 60_000,
  });
  if (!enabled) return null;
  return data;
}
