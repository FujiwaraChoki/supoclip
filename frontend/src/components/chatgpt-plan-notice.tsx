"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

export function ChatGPTPlanNotice({ userId }: { userId?: string }) {
  const [active, setActive] = useState(false);
  useEffect(() => {
    if (!userId || process.env.NEXT_PUBLIC_SELF_HOST === "false") return;
    const controller = new AbortController();
    void fetch("/api/chatgpt/status", { cache: "no-store", signal: controller.signal })
      .then(async (response) => response.ok ? response.json() : null)
      .then((data) => setActive(Boolean(data?.accounts?.some((account: { enabled: boolean }) => account.enabled))))
      .catch(() => {});
    return () => controller.abort();
  }, [userId]);
  if (!active) return null;
  return <p className="mt-3 text-center text-xs text-muted-foreground">
    Using ChatGPT plan for clip analysis · <Link className="underline" href="/settings/chatgpt">Change model</Link>
    {" · "}<a className="underline" href="https://chatgpt.com/settings/usage" target="_blank" rel="noopener noreferrer">Manage usage</a>
  </p>;
}
