"use client";

import dynamic from "next/dynamic";

// Load dashboard directly - no login required for self-hosted usage
const HomeApp = dynamic(() => import("@/components/home-app"), {
  ssr: false,
});

export function HomeRouter() {
  // Skip authentication, load dashboard directly
  return <HomeApp />;
}
