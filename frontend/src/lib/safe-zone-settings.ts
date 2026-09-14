/** Per-project + global-default persistence for the Safe Zone Overlay toggle. */

import type { SafeZoneSelection } from "@/lib/safe-zones";

const GLOBAL_DEFAULT_KEY = "supoclip:safe-zones:default-platform";

export interface SafeZoneProjectState {
  enabled: boolean;
  platform: SafeZoneSelection;
}

function projectKey(taskId: string) {
  return `supoclip:safe-zones:project:${taskId}`;
}

export function getSafeZoneProjectState(taskId: string): SafeZoneProjectState | null {
  try {
    const raw = localStorage.getItem(projectKey(taskId));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (typeof parsed?.enabled === "boolean" && typeof parsed?.platform === "string") {
      return parsed as SafeZoneProjectState;
    }
    return null;
  } catch {
    return null;
  }
}

export function setSafeZoneProjectState(taskId: string, state: SafeZoneProjectState) {
  try {
    localStorage.setItem(projectKey(taskId), JSON.stringify(state));
  } catch {
    // Private browsing / storage disabled — the toggle just won't persist.
  }
}

export function getDefaultSafeZonePlatform(): SafeZoneSelection {
  try {
    const raw = localStorage.getItem(GLOBAL_DEFAULT_KEY);
    return raw ? (raw as SafeZoneSelection) : "all";
  } catch {
    return "all";
  }
}

export function setDefaultSafeZonePlatform(platform: SafeZoneSelection) {
  try {
    localStorage.setItem(GLOBAL_DEFAULT_KEY, platform);
  } catch {
    // Private browsing / storage disabled.
  }
}
