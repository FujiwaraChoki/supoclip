"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchOnboardingStatus, onboardingKeys, type OnboardingStatus } from "@/lib/onboarding";

/** `?onboarding=pebble|classic` (admins) shows either onboarding; remembered for this tab. */
function override(): string | null {
  if (typeof window === "undefined") return null;
  const fromUrl = new URLSearchParams(window.location.search).get("onboarding");
  try {
    if (fromUrl === "pebble" || fromUrl === "classic") window.sessionStorage.setItem("supoclip.onboarding.override", fromUrl);
    return fromUrl ?? window.sessionStorage.getItem("supoclip.onboarding.override");
  } catch {
    return fromUrl;
  }
}

/**
 * The account's onboarding group and progress. `undefined` while loading; `null` when it couldn't
 * be read, which shows the classic experience.
 */
export function useOnboardingStatus(enabled = true): OnboardingStatus | null | undefined {
  const { data, isError } = useQuery({
    queryKey: onboardingKeys.status,
    queryFn: () => fetchOnboardingStatus(override()),
    enabled,
    staleTime: 60_000,
    retry: 1,
  });
  if (!enabled || isError) return null;
  return data;
}
