"use client";

import dynamic from "next/dynamic";
import { useSession } from "@/lib/auth-client";

const HomeApp = dynamic(() => import("@/components/home-app"), {
  ssr: false,
});

export function HomeRouter() {
  const { data: session, isPending } = useSession();

  // For self-hosted usage without login, always show dashboard
  // The useSession hook returns mock user if no real session exists
  if (!isPending) {
    return <HomeApp />;
  }

  return null;
}
