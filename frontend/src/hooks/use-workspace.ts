"use client";

import { useCallback, useEffect, useState } from "react";
import { invalidateBillingSummary, WORKSPACE_CHANGED_EVENT } from "@/hooks/use-billing-summary";

export type TeamRole = "owner" | "admin" | "member";

export interface TeamSummary {
  id: string;
  name: string;
  slug: string;
  ownerId: string;
  role: TeamRole;
  plan: string;
  subscriptionStatus: string;
  seatLimit: number;
  memberCount: number;
  pendingInviteCount: number;
  occupiedSeats: number;
}

export interface WorkspacePayload {
  activeTeamId: string | null;
  teams: TeamSummary[];
}

let pendingWorkspace: Promise<WorkspacePayload | null> | null = null;
let cachedWorkspace: WorkspacePayload | null = null;

function loadWorkspaces(force = false): Promise<WorkspacePayload | null> {
  if (force) {
    pendingWorkspace = null;
    cachedWorkspace = null;
  }
  pendingWorkspace ??= fetch("/api/teams", { cache: "no-store" })
    .then(async (response) => {
      if (!response.ok) return null;
      const data = (await response.json()) as WorkspacePayload;
      cachedWorkspace = data;
      return data;
    })
    .catch(() => null)
    .finally(() => {
      setTimeout(() => {
        pendingWorkspace = null;
      }, 3_000);
    });
  return pendingWorkspace;
}

export function invalidateWorkspaces() {
  pendingWorkspace = null;
  cachedWorkspace = null;
  invalidateBillingSummary();
}

export function useWorkspace(enabled: boolean) {
  const [data, setData] = useState<WorkspacePayload | null>(() => cachedWorkspace);
  const [loading, setLoading] = useState<boolean>(enabled && !cachedWorkspace);

  const refresh = useCallback(async () => {
    if (!enabled) return null;
    const next = await loadWorkspaces(true);
    setData(next);
    setLoading(false);
    return next;
  }, [enabled]);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    const run = (force = false) => {
      void loadWorkspaces(force).then((next) => {
        if (!cancelled) {
          setData(next);
          setLoading(false);
        }
      });
    };
    run();
    const onChanged = () => run(true);
    window.addEventListener(WORKSPACE_CHANGED_EVENT, onChanged);
    return () => {
      cancelled = true;
      window.removeEventListener(WORKSPACE_CHANGED_EVENT, onChanged);
    };
  }, [enabled]);

  const switchWorkspace = useCallback(async (teamId: string | null, redirectTo?: string) => {
    const response = await fetch("/api/teams/active", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ teamId }),
    });
    if (!response.ok) {
      const payload = (await response.json().catch(() => ({}))) as { error?: string };
      throw new Error(payload.error || "Could not switch workspace");
    }
    invalidateWorkspaces();
    if (redirectTo) {
      window.location.assign(redirectTo);
      return;
    }
    if (window.location.pathname.startsWith("/tasks/")) {
      window.location.assign("/list");
      return;
    }
    window.location.reload();
  }, []);

  const createTeam = useCallback(async (name: string) => {
    const response = await fetch("/api/teams", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const payload = (await response.json().catch(() => ({}))) as {
      team?: TeamSummary;
      error?: string;
    };
    if (!response.ok || !payload.team) {
      throw new Error(payload.error || "Could not create team");
    }
    invalidateWorkspaces();
    return payload.team;
  }, []);

  const teams = data?.teams ?? [];
  const activeTeamId = data?.activeTeamId ?? null;
  const activeTeam = activeTeamId ? teams.find((team) => team.id === activeTeamId) ?? null : null;

  return {
    activeTeamId,
    activeTeam,
    teams,
    loading,
    refresh,
    switchWorkspace,
    createTeam,
  };
}
