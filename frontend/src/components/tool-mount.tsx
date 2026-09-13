"use client";

import { useEffect, useRef } from "react";

import type { Tool } from "@/tools/types";

/**
 * Hosts a `Tool` that implements `mount(container)` (a route-owning tool,
 * using `href` instead, is rendered by its own page under app/ and never
 * reaches this component). Calls `mount` once the container exists, and its
 * returned cleanup (if any) when this unmounts or `tool` changes.
 */
export function ToolMount({ tool }: { tool: Tool }) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !tool.mount) return;
    const cleanup = tool.mount(container);
    return () => cleanup?.();
  }, [tool]);

  return <div ref={containerRef} />;
}
