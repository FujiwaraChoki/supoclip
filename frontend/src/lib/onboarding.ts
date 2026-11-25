/**
 * Onboarding for new accounts (docs/onboarding.md): whether they get Agent's guided onboarding,
 * what they make, a look at their video, and the free preview clip. Backend routes are
 * /onboarding/* and /tasks/{id}/unlock.
 */
import type { BillingPlanId, PublicBillingPlan } from "@/lib/billing-plans";

export type Makes = "podcasts" | "streams" | "youtube" | "talks" | "other";
export const MAKES: Makes[] = ["podcasts", "streams", "youtube", "talks", "other"];

export interface LockedMoment {
  position: number;
  hook_title: string | null;
  virality_score: number | null;
  duration: number | null;
  start: number;
}

/** `tasks.preview`: set on a free preview project. */
export interface PreviewState {
  status?: string;
  found?: number;
  rendered_position?: number;
  locked?: LockedMoment[];
  unlocked_at?: string;
}

export type PreviewRefusal = "disabled" | "not_needed" | "team" | "unverified" | "used" | "running" | "busy";

export interface OnboardingStatus {
  /** `pebble`: the account gets Agent's guided onboarding. */
  variant: "pebble" | null;
  completed: boolean;
  answers: { makes?: Makes };
  monetized: boolean;
  preview: {
    eligible: boolean;
    reason: PreviewRefusal | null;
    max_minutes: number;
    task: { id: string; status: string; preview: PreviewState | null } | null;
  };
}

export interface LinkLook {
  url: string;
  source_type: "youtube" | "twitch" | "kick";
  title: string | null;
  channel: string | null;
  duration_seconds: number;
  thumbnail_url: string | null;
  estimated_clips: number | null;
  preview: { eligible: boolean; reason: PreviewRefusal | null; max_minutes: number; fits: boolean };
}

export const onboardingKeys = { status: ["onboarding", "status"] as const };

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    let code: string | undefined;
    try {
      const body = await response.json();
      const detail = body?.detail ?? body;
      if (typeof detail === "string") message = detail;
      else if (detail?.message) { message = detail.message; code = detail.code; }
    } catch { /* keep the status message */ }
    throw Object.assign(new Error(message), { status: response.status, code });
  }
  return (await response.json()) as T;
}

/** `?onboarding=pebble` lets admins see the guided onboarding (the server ignores it for others). */
export async function fetchOnboardingStatus(override?: string | null): Promise<OnboardingStatus | null> {
  const query = override === "pebble" ? "?variant=pebble" : "";
  const response = await fetch(`/api/onboarding/status${query}`, { cache: "no-store" });
  if (response.status === 404) return null;
  return json<OnboardingStatus>(response);
}

export async function saveMakes(makes: Makes) {
  await json(await fetch("/api/onboarding/answers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ makes }) }));
}

export async function completeOnboarding() {
  await fetch("/api/onboarding/complete", { method: "POST", keepalive: true }).catch(() => {});
}

export async function lookAtLink(url: string): Promise<LinkLook> {
  return json<LinkLook>(await fetch("/api/onboarding/link", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }) }));
}

export async function unlockPreview(taskId: string): Promise<{ job_id: string | null }> {
  return json(await fetch(`/api/tasks/${encodeURIComponent(taskId)}/unlock`, { method: "POST" }));
}

/**
 * "Unlock this preview once I have a plan": remembered on the server before checkout, so the rest of
 * the clips are made even if this tab is closed by the time the payment lands.
 */
export async function requestUnlockWhenPaid(taskId: string): Promise<void> {
  await fetch(`/api/tasks/${encodeURIComponent(taskId)}/unlock`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ when_paid: true }),
    keepalive: true,
  }).catch(() => {});
}

/** The preview state of a task row, or null (rows may carry it as text). */
export function previewOf(task: { preview?: unknown } | null | undefined): PreviewState | null {
  let value = task?.preview;
  if (typeof value === "string") {
    try { value = JSON.parse(value); } catch { return null; }
  }
  return value && typeof value === "object" ? (value as PreviewState) : null;
}

export function isLockedPreview(task: { preview?: unknown } | null | undefined): boolean {
  const state = previewOf(task);
  return Boolean(state && !state.unlocked_at);
}

/**
 * The plan Agent suggests: Creator when the video is longer than Pro takes in one go, for streamers
 * (long VODs), or when Pro's monthly hours wouldn't cover about four videos like it; Pro otherwise.
 */
export function suggestedPlan(plans: PublicBillingPlan[], makes: Makes | undefined, durationSeconds: number | null | undefined): BillingPlanId {
  const pro = plans.find((plan) => plan.id === "pro");
  const minutes = (durationSeconds ?? 0) / 60;
  if (!pro) return "scale";
  if (makes === "streams") return "scale";
  if (minutes > pro.youtubeMaxMinutes) return "scale";
  if (pro.minuteLimit > 0 && minutes * 4 > pro.minuteLimit) return "scale";
  return "pro";
}

export function formatRuntime(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

/** About how many clips a video holds: one per five minutes, five to twenty (backend `max_final_clips_for_duration`). */
export function estimateClips(durationSeconds: number, maxClips = 10): number {
  const ceiling = Math.max(1, Math.min(20, maxClips));
  return Math.max(Math.min(5, ceiling), Math.min(ceiling, Math.ceil(Math.max(0, durationSeconds) / 300)));
}
