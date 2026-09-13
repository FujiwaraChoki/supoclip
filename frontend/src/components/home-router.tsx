"use client";

import dynamic from "next/dynamic";

const HomeApp = dynamic(() => import("@/components/home-app"), {
  ssr: false,
});

// Local-first: no login, so there's nothing to route on — always show the app.
export function HomeRouter() {
  return <HomeApp />;
}
