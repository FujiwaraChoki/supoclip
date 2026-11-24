"use client";

import { useCallback, useSyncExternalStore } from "react";

import type { LinkLook, Makes } from "@/lib/onboarding";

/**
 * Where someone is in the Pebble onboarding, kept in this browser so it survives reloads, page
 * changes and the trip to Stripe Checkout and back. The server holds the parts that matter across
 * devices (the group, the answers, the preview project, whether it ended).
 *
 * intro   the opening chat on home (hello, what do you make, examples)
 * link    docked: paste a link, Agent looks at the video, Generate
 * making  the free preview is being made
 * ready   the preview is ready: the best clip, the locked rest, the plans
 * paid    back from checkout: unlocking the rest
 * choose  Creator: agent or form as home (Pro skips to done)
 * done    finished; the dock says goodbye and goes away
 */
export type PebbleStage = "intro" | "link" | "making" | "ready" | "paid" | "choose" | "done";

export interface PebbleFlow {
  stage: PebbleStage;
  makes?: Makes;
  taskId?: string;
  link?: LinkLook | null;
  /** The dock is folded into a bubble. */
  minimized?: boolean;
}

const INITIAL: PebbleFlow = { stage: "intro" };
const listeners = new Set<() => void>();
const cache = new Map<string, { raw: string | null; value: PebbleFlow }>();

function key(userId: string) {
  return `supoclip.pebble.v1.${userId}`;
}

function read(userId: string): PebbleFlow {
  let raw: string | null = null;
  try { raw = window.localStorage.getItem(key(userId)); } catch { /* storage blocked */ }
  const cached = cache.get(userId);
  if (cached && cached.raw === raw) return cached.value;
  let value = INITIAL;
  try {
    const parsed = raw ? (JSON.parse(raw) as PebbleFlow) : null;
    if (parsed && typeof parsed.stage === "string") value = parsed;
  } catch { /* start over */ }
  cache.set(userId, { raw, value });
  return value;
}

export function writePebbleFlow(userId: string, next: PebbleFlow) {
  try { window.localStorage.setItem(key(userId), JSON.stringify(next)); } catch { /* storage blocked: this tab still follows */ }
  cache.set(userId, { raw: JSON.stringify(next), value: next });
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => { if (event.key?.startsWith("supoclip.pebble.")) listener(); };
  window.addEventListener("storage", onStorage);
  return () => { listeners.delete(listener); window.removeEventListener("storage", onStorage); };
}

/** The flow for this account and a function that merges changes into it. */
export function usePebbleFlow(userId: string | null | undefined): [PebbleFlow, (patch: Partial<PebbleFlow>) => void] {
  const flow = useSyncExternalStore(
    subscribe,
    () => (userId ? read(userId) : INITIAL),
    () => INITIAL,
  );
  const update = useCallback((patch: Partial<PebbleFlow>) => {
    if (!userId) return;
    writePebbleFlow(userId, { ...read(userId), ...patch });
  }, [userId]);
  return [flow, update];
}
