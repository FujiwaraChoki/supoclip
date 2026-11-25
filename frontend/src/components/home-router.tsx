"use client";

import { Suspense } from "react";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";

import LandingPage from "@/components/landing-page";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgentStatus } from "@/hooks/use-agent-status";
import { useSession } from "@/lib/auth-client";
import { isLandingOnlyModeEnabled } from "@/lib/app-flags";
import { agentKeys, agentOffered, type AgentStatus } from "@/lib/agent";
import { usePebbleFlow } from "@/components/onboarding/pebble-flow";
import { useOnboardingStatus } from "@/hooks/use-onboarding-status";
import { trackEvent } from "@/lib/analytics";
import { completeOnboarding, onboardingKeys, type OnboardingStatus } from "@/lib/onboarding";

const HomeApp = dynamic(() => import("@/components/home-app"), { ssr: false });
const AgentHome = dynamic(() => import("@/components/agent/agent-home").then((m) => m.AgentHome), { ssr: false });
const PebbleIntro = dynamic(() => import("@/components/onboarding/pebble-intro").then((m) => m.PebbleIntro), { ssr: false });
const HomeModeChoice = dynamic(() => import("@/components/agent/home-mode-choice").then((m) => m.HomeModeChoice), { ssr: false });

function HomeSkeleton() {
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-2xl space-y-4">
        <Skeleton className="mx-auto h-9 w-64" />
        <Skeleton className="h-36 w-full rounded-2xl" />
      </div>
    </div>
  );
}

export type LockedAgent = { reason: "plan" | "trial_used"; /** Home was the agent before the plan changed. */ lapsed: boolean };

export type HomeScreen =
  | { screen: "loading" }
  | { screen: "form"; agentLink?: string; agentLocked?: LockedAgent }
  | { screen: "choice" }
  | { screen: "agent"; manualHref: string };

/**
 * Signed-in home: the agent or the create form, as the person chose (asked once, changed in
 * Settings). `?mode=manual` / `?mode=agent` opens the other one just this time.
 *
 * The choice is offered to new accounts and Creators (`offer_choice`). Without Creator the agent
 * stays visible but locked, so the form carries a link that opens the upgrade dialog instead.
 */
export function homeScreenFor(status: AgentStatus | null | undefined, once: string | null): HomeScreen {
  if (status === undefined) return { screen: "loading" };
  if (!status || !agentOffered(status)) return { screen: "form" };
  const oneTime = once === "manual" || once === "agent";
  if (status.home_mode === null && status.offer_choice && !oneTime) return { screen: "choice" };
  if (!status.available) {
    const reason = status.reason === "trial_used" ? "trial_used" : "plan";
    return { screen: "form", agentLocked: { reason, lapsed: status.home_mode === "agent" } };
  }
  // Accounts that were never asked keep the form they know, with the agent one tap away.
  const preferred = status.home_mode ?? (status.offer_choice ? "agent" : "manual");
  const showAgent = once === "agent" || (once !== "manual" && preferred === "agent");
  if (showAgent) return { screen: "agent", manualHref: preferred === "agent" ? "/?mode=manual" : "/" };
  return { screen: "form", agentLink: preferred === "agent" ? "/" : "/?mode=agent" };
}

function SignedInHome() {
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const { data: session } = useSession();
  const status = useAgentStatus();
  const onboarding = useOnboardingStatus();
  const [flow, updateFlow] = usePebbleFlow(session?.user?.id);
  if (status === undefined || onboarding === undefined) return <HomeSkeleton />;

  // Agent's guided onboarding: the intro first, then the create form with Agent docked beside it.
  // It never shows the "How do you want to work?" screen (Agent asks in the dock).
  const pebble = onboarding?.variant === "pebble";
  if (pebble && onboarding && !onboarding.completed && flow.stage === "intro" && params.get("mode") === null) {
    return (
      <PebbleIntro
        status={onboarding}
        onStart={(makes) => updateFlow({ stage: "link", makes, minimized: false })}
        onSkip={() => {
          trackEvent("onboarding_completed", { skipped: true, stage: "intro" });
          void completeOnboarding();
          queryClient.setQueryData<OnboardingStatus | null>(onboardingKeys.status, (current) => (current ? { ...current, completed: true } : current));
          updateFlow({ stage: "done", minimized: true });
        }}
      />
    );
  }
  const home = homeScreenFor(pebble && status ? { ...status, offer_choice: false } : status, params.get("mode"));
  const preview = pebble && onboarding?.preview.eligible ? { active: true } : undefined;
  switch (home.screen) {
    case "loading":
      return <HomeSkeleton />;
    case "choice":
      return (
        <HomeModeChoice
          status={status!}
          onChosen={(mode) => queryClient.setQueryData<AgentStatus>(agentKeys.status, { ...status!, home_mode: mode })}
        />
      );
    case "agent":
      return <AgentHome manualHref={home.manualHref} />;
    default:
      return <HomeApp agentLink={home.agentLink} agentLocked={home.agentLocked} preview={preview} />;
  }
}

export function HomeRouter() {
  const { data: session, isPending } = useSession();

  if (!isLandingOnlyModeEnabled && !isPending && session?.user) {
    return (
      <Suspense fallback={<HomeSkeleton />}>
        <SignedInHome />
      </Suspense>
    );
  }

  return <LandingPage />;
}
