"use client";

import dynamic from "next/dynamic";

import LandingPage from "@/components/landing-page";
import { useSession } from "@/lib/auth-client";
import { isLandingOnlyModeEnabled } from "@/lib/app-flags";

const HomeApp = dynamic(() => import("@/components/home-app"), {
  ssr: false,
});

export function HomeRouter() {
  const { data: session, isPending } = useSession();

  if (!isLandingOnlyModeEnabled && !isPending && session?.user) {
    return <HomeApp />;
  }

  return <LandingPage />;
}
