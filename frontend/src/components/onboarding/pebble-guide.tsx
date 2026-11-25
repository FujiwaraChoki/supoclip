"use client";

import { PebbleDock, pebbleDockActive } from "@/components/onboarding/pebble-dock";
import { usePebbleFlow } from "@/components/onboarding/pebble-flow";
import { PebbleHints } from "@/components/onboarding/pebble-hints";
import { useOnboardingStatus } from "@/hooks/use-onboarding-status";
import { useSession } from "@/lib/auth-client";

/**
 * Agent's onboarding, mounted by the app shell on every signed-in page: the docked guide while a new
 * account makes its first clip, then a hint the first time each page is opened. Only accounts that
 * get the guided onboarding see either.
 */
export function PebbleGuide({ pathname, navigate, immersive }: { pathname: string; navigate: (path: string) => void; immersive: boolean }) {
  const { data: session } = useSession();
  const userId = session?.user?.id;
  const status = useOnboardingStatus(Boolean(userId));
  const [flow] = usePebbleFlow(userId);

  if (!userId || !status || status.variant !== "pebble") return null;
  const guiding = !status.completed && pebbleDockActive(flow.stage);
  const hintsOn = !guiding && (status.completed || flow.stage === "done");
  return (
    <>
      {guiding && !immersive && <PebbleDock status={status} userId={userId} pathname={pathname} navigate={navigate} />}
      <PebbleHints pathname={pathname} enabled={hintsOn} />
    </>
  );
}
