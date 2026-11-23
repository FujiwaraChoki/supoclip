"use client";

import { useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";

/**
 * Asks `queryFn` now and then every `intervalMs` until `done` holds for its answer or it has
 * answered `attempts` times. Used after a checkout redirect, when Stripe's webhook can land a
 * moment after the person does. `queryFn` must not throw: a failed request answers null.
 */
export function usePollUntil<T>({
  queryKey,
  queryFn,
  enabled,
  done,
  attempts,
  intervalMs,
}: {
  queryKey: QueryKey;
  queryFn: () => Promise<T | null>;
  enabled: boolean;
  done: (value: T) => boolean;
  attempts: number;
  intervalMs: number;
}) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey,
    queryFn,
    enabled,
    gcTime: 0,
    retry: false,
    // A checkout tab is often in the background while Stripe finishes; keep counting there too.
    refetchIntervalInBackground: true,
    // Only the interval asks: a focus or reconnect would spend attempts early.
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchInterval: (current) => {
      const data = current.state.data;
      if (data && done(data)) return false;
      return current.state.dataUpdateCount >= attempts ? false : intervalMs;
    },
  });
  // Read on every answer: `dataUpdatedAt` changes each time, even when the value is the same.
  const answered = query.dataUpdatedAt ? queryClient.getQueryState(queryKey)?.dataUpdateCount ?? 0 : 0;
  return { data: query.data ?? null, answered, exhausted: answered >= attempts };
}
