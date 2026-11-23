"use client";

import { useQueryClient, type QueryClient, type QueryKey } from "@tanstack/react-query";
import { useEffect } from "react";

type Listener = { count: number; dispose: () => void };

// One window listener per (query client, event, key), however many components use the hook,
// so an event triggers one refetch instead of several that cancel or join each other.
const registry = new WeakMap<QueryClient, Map<string, Listener>>();

/** Refetch `queryKey` whenever `event` fires on `window` (while `enabled`). */
export function useInvalidateOnWindowEvent(event: string, queryKey: QueryKey, enabled = true) {
  const queryClient = useQueryClient();
  const id = `${event}\u0000${JSON.stringify(queryKey)}`;

  useEffect(() => {
    if (!enabled) return;
    let listeners = registry.get(queryClient);
    if (!listeners) {
      listeners = new Map();
      registry.set(queryClient, listeners);
    }
    let entry = listeners.get(id);
    if (!entry) {
      const key = JSON.parse(id.slice(id.indexOf("\u0000") + 1)) as QueryKey;
      // The default cancelRefetch: true drops a fetch started before the change, so the
      // refetch reads the state after it.
      const onEvent = () => { void queryClient.invalidateQueries({ queryKey: key }); };
      window.addEventListener(event, onEvent);
      entry = { count: 0, dispose: () => window.removeEventListener(event, onEvent) };
      listeners.set(id, entry);
    }
    entry.count += 1;
    const current = entry;
    const map = listeners;
    return () => {
      current.count -= 1;
      if (current.count === 0) {
        current.dispose();
        map.delete(id);
      }
    };
  }, [queryClient, event, id, enabled]);
}
