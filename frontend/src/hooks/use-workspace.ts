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
  /** False on a hosted install that cannot sell the Team plan yet: no new teams there. */
  available?: boolean;
}

/** A failed workspace request: the server's message (may be empty) and its error code. */
export class WorkspaceRequestError extends Error {
  code?: string;

  constructor(message: string, code?: string) {
    super(message);
    this.name = "WorkspaceRequestError";
    this.code = code;
  }
}

let pendingWorkspace: Promise<WorkspacePayload | null> | null = null;
let cachedWorkspace: WorkspacePayload | null = null;

/**
 * The last good answer stays until a newer one arrives: a request that fails on the way
 * (a blip, a 500) must not hide the switcher or turn Team settings into "Create a team".
 * Only a 401 forgets it, because then nobody is signed in.
 */
function loadWorkspaces(force = false): Promise<WorkspacePayload | null> {
  if (force) pendingWorkspace = null;
  pendingWorkspace ??= fetch("/api/teams", { cache: "no-store" })
    .then(async (response) => {
      if (response.status === 401) {
        cachedWorkspace = null;
        return null;
      }
      if (!response.ok) return cachedWorkspace;
      cachedWorkspace = (await response.json()) as WorkspacePayload;
      return cachedWorkspace;
    })
    .catch(() => cachedWorkspace)
    .finally(() => {
      setTimeout(() => {
        pendingWorkspace = null;
      }, 3_000);
    });
  return pendingWorkspace;
}

export function invalidateWorkspaces() {
  pendingWorkspace = null;
  invalidateBillingSummary();
}

// The active workspace is a cookie, so it changes for every tab at once. Tell the other
// tabs, or they keep showing one workspace while their next request acts in another.
const WORKSPACE_SWITCH_CHANNEL = "supoclip:workspace-switch";
let switchChannel: BroadcastChannel | null | undefined;

function onSwitchedInOtherTab() {
  // A task stays reachable from any workspace its owner or team allows, so an open task or
  // editor is left alone (it may hold work in progress); only its shell catches up.
  if (window.location.pathname.startsWith("/tasks/")) invalidateWorkspaces();
  else window.location.reload();
}

/** One channel per tab: a channel never hears its own messages, so this tab is not told. */
function workspaceSwitchChannel(): BroadcastChannel | null {
  if (switchChannel !== undefined) return switchChannel;
  if (typeof BroadcastChannel === "undefined") {
    switchChannel = null;
    // A `storage` event fires in every other tab of this origin.
    window.addEventListener("storage", (event) => {
      if (event.key === WORKSPACE_SWITCH_CHANNEL && event.newValue) onSwitchedInOtherTab();
    });
    return null;
  }
  switchChannel = new BroadcastChannel(WORKSPACE_SWITCH_CHANNEL);
  switchChannel.addEventListener("message", onSwitchedInOtherTab);
  return switchChannel;
}

/** Call after the workspace cookie changed (switch, new team, leaving or deleting the active team). */
export function announceWorkspaceSwitch() {
  try {
    const channel = workspaceSwitchChannel();
    if (channel) channel.postMessage("switched");
    else window.localStorage.setItem(WORKSPACE_SWITCH_CHANNEL, String(Date.now()));
  } catch {
    // Storage can be blocked; this tab still switches.
  }
}

/** The server's reason when it gave one. Callers fall back to their own translated message. */
async function requestError(response: Response): Promise<WorkspaceRequestError> {
  const payload = (await response.json().catch(() => ({}))) as { error?: string; code?: string };
  return new WorkspaceRequestError(
    typeof payload.error === "string" ? payload.error : "",
    typeof payload.code === "string" ? payload.code : undefined,
  );
}

export function useWorkspace(enabled: boolean) {
  const [data, setData] = useState<WorkspacePayload | null>(() => cachedWorkspace);
  // True once a request has answered. Until then nothing is known, which is not "no teams".
  const [settled, setSettled] = useState<boolean>(() => cachedWorkspace !== null);

  const refresh = useCallback(async () => {
    if (!enabled) return null;
    const next = await loadWorkspaces(true);
    setData(next);
    setSettled(true);
    return next;
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    workspaceSwitchChannel();
    let cancelled = false;
    const run = (force = false) => {
      void loadWorkspaces(force).then((next) => {
        if (!cancelled) {
          setData(next);
          setSettled(true);
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
    if (!response.ok) throw await requestError(response);
    invalidateWorkspaces();
    announceWorkspaceSwitch();
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
    if (!response.ok) throw await requestError(response);
    const payload = (await response.json().catch(() => ({}))) as { team?: TeamSummary };
    if (!payload.team) throw new WorkspaceRequestError("");
    invalidateWorkspaces();
    // Creating a team makes it the active workspace.
    announceWorkspaceSwitch();
    return payload.team;
  }, []);

  const teams = data?.teams ?? [];
  const activeTeamId = data?.activeTeamId ?? null;
  const activeTeam = activeTeamId ? teams.find((team) => team.id === activeTeamId) ?? null : null;

  return {
    activeTeamId,
    activeTeam,
    teams,
    // Whether a new team can be created here; only an explicit "no" from the server hides it.
    teamsAvailable: data?.available !== false,
    // Derived, so it is already true on the render where `enabled` turns on.
    loading: enabled && !settled,
    refresh,
    switchWorkspace,
    createTeam,
  };
}

/** The signed-in user's role in `teamId`; null when there is no team or the role is not known yet. */
export function useTeamRole(teamId: string | null | undefined): TeamRole | null {
  const { teams } = useWorkspace(Boolean(teamId));
  return teamId ? teams.find((team) => team.id === teamId)?.role ?? null : null;
}
