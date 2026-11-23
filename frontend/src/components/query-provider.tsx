"use client";

import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { WORKSPACE_SWITCHED_EVENT } from "@/hooks/use-workspace";
import { useSession } from "@/lib/auth-client";

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: 1 },
    },
  });
}

/**
 * Keeps the cache to one account and one workspace. Signing in, out or as someone else
 * happens without a page load, so everything cached for the previous user is dropped.
 * A workspace switch made in another tab (while this one keeps its page) drops what
 * isn't on screen and refetches what is.
 */
export function QueryScope() {
  const queryClient = useQueryClient();
  const { data: session, isPending } = useSession();
  const userId = isPending ? undefined : session?.user?.id ?? null;
  const settledUser = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    if (userId === undefined) return;
    if (settledUser.current !== undefined && settledUser.current !== userId) {
      // clear() would leave mounted queries attached to removed entries, still showing the
      // previous user's data; resetting them empties the screen and refetches as the new user.
      queryClient.removeQueries({ type: "inactive" });
      queryClient.getMutationCache().clear();
      void queryClient.resetQueries({ type: "active" });
    }
    settledUser.current = userId;
  }, [userId, queryClient]);

  useEffect(() => {
    const onSwitched = () => {
      queryClient.removeQueries({ type: "inactive" });
      void queryClient.invalidateQueries({ type: "active" });
    };
    window.addEventListener(WORKSPACE_SWITCHED_EVENT, onSwitched);
    return () => window.removeEventListener(WORKSPACE_SWITCHED_EVENT, onSwitched);
  }, [queryClient]);

  return null;
}

/** One client per browser tab; shared by every page that uses `useQuery`. */
export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(makeQueryClient);
  return (
    <QueryClientProvider client={client}>
      <QueryScope />
      {children}
    </QueryClientProvider>
  );
}
