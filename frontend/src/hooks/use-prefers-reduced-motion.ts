"use client";

import { useSyncExternalStore } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

function subscribe(onChange: () => void) {
  const media = window.matchMedia?.(QUERY);
  media?.addEventListener?.("change", onChange);
  return () => media?.removeEventListener?.("change", onChange);
}

const getSnapshot = () => window.matchMedia?.(QUERY).matches ?? false;
const getServerSnapshot = () => false;

/**
 * Whether the visitor asked for reduced motion. Use this instead of motion's
 * `useReducedMotion` in server-rendered components: that one returns the real
 * setting on the first client render, so markup that differs by it fails to
 * hydrate (React #418). This one hydrates as `false`, like the server, then
 * switches to the visitor's setting and follows later changes.
 */
export function usePrefersReducedMotion() {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
