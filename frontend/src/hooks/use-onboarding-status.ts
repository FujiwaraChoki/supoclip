"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchOnboardingStatus, onboardingKeys, type OnboardingStatus } from "@/lib/onboarding";

/** `?onboarding=pebble` (admins) shows the guided onboarding; remembered for this tab. */
function override(): string | null {
  if (typeof window === "undefined") return null;
  const fromUrl = new URLSearchParams(window.location.search).get("onboarding");
  try {
    if (fromUrl === "pebble") window.sessionStorage.setItem("supoclip.onboarding.override", fromUrl);
    return fromUrl ?? window.sessionStorage.getItem("supoclip.onboarding.override");
  } catch {
    return fromUrl;
  }
}

/**
 * Whether the account gets Agent's guided onboarding, and its progress. `undefined` while loading;
 * `null` when it couldn't be read, which shows the standard home.
 *
 * A failure is cached as `null` rather than left as a query error: react-query puts an errored
 * query without data back to `pending` whenever another component mounts it (the app shell and
 * the guide do), which would flip home back to its skeleton, unmount the form, and start over.
 */
export function useOnboardingStatus(enabled = true): OnboardingStatus | null | undefined {
  const { data } = useQuery({
    queryKey: onboardingKeys.status,
    queryFn: () => fetchOnboardingStatus(override()).catch(() => null),
    enabled,
    staleTime: 60_000,
  });
  if (!enabled) return null;
  return data;
}
