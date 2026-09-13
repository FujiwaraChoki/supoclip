import type { HookStyle } from "@/lib/hook-style";
import type { BrollSettings, SocialOverlay, TargetDuration } from "@/lib/retention-settings";

export type TaskGenerationSettings = {
  fontFamily: string | null;
  fontSize: number | null;
  fontColor: string | null;
  captionTemplate: string;
  outputFormat: string;
  addSubtitles: boolean;
  cutLongPauses: boolean;
  pauseThresholdMs: string;
  removeFillerWords: boolean;
  filteredWords: string;
  /** 0-100 slider (0 = off, 100 = most aggressive); null = use the manual toggles above. */
  cleanupSensitivity: number | null;
  hookStyle: HookStyle;
  socialOverlay: SocialOverlay;
  brollSettings: BrollSettings;
  targetDuration: TargetDuration;
  clipCount: number | null;
};

export type TaskPreset = {
  name: string;
  settings: TaskGenerationSettings;
};

const PRESETS_KEY = "supoclip:task-presets";
const LAST_SETTINGS_KEY = "supoclip:last-task-settings";

function safeParse<T>(raw: string | null): T | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

/** All localStorage access is wrapped: private browsing / disabled storage must never break the form. */
export function listPresets(): TaskPreset[] {
  if (typeof window === "undefined") return [];
  try {
    return safeParse<TaskPreset[]>(window.localStorage.getItem(PRESETS_KEY)) ?? [];
  } catch {
    return [];
  }
}

export function savePreset(name: string, settings: TaskGenerationSettings): void {
  if (typeof window === "undefined" || !name.trim()) return;
  try {
    const presets = listPresets().filter((p) => p.name !== name);
    presets.push({ name: name.trim(), settings });
    window.localStorage.setItem(PRESETS_KEY, JSON.stringify(presets));
  } catch {
    // Storage unavailable (private browsing, quota, etc.) — silently no-op.
  }
}

export function deletePreset(name: string): void {
  if (typeof window === "undefined") return;
  try {
    const presets = listPresets().filter((p) => p.name !== name);
    window.localStorage.setItem(PRESETS_KEY, JSON.stringify(presets));
  } catch {
    // ignore
  }
}

export function loadLastSettings(): TaskGenerationSettings | null {
  if (typeof window === "undefined") return null;
  try {
    return safeParse<TaskGenerationSettings>(window.localStorage.getItem(LAST_SETTINGS_KEY));
  } catch {
    return null;
  }
}

export function saveLastSettings(settings: TaskGenerationSettings): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(LAST_SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // ignore
  }
}
