"use client";

import { createRoot, type Root } from "react-dom/client";
import { Sparkles } from "lucide-react";

import type { Tool } from "../types";

/**
 * Stand-in for every future tool (ranking/compilation, voiceover/animation,
 * etc.) — deliberately generic and not named after any specific one of
 * them. Simple enough to genuinely implement `mount(container)` by
 * rendering straight into it, unlike a route-owning tool (see Tool's
 * doc-comment) — a real future tool built as its own route subtree would
 * follow Clipping's pattern (`href`) instead of this one.
 */
function ComingSoon() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-24 text-center px-4">
      <Sparkles className="w-10 h-10 text-muted-foreground" />
      <p className="font-medium text-foreground">More tools are coming</p>
      <p className="text-sm text-muted-foreground max-w-sm">
        SupoClip is growing beyond clipping. This space is reserved for what&apos;s next.
      </p>
    </div>
  );
}

export const placeholderTool: Tool = {
  id: "placeholder",
  name: "More Tools",
  icon: Sparkles,
  description: "Coming soon",
  thumbnail: "/assets/tools/placeholder.svg",
  mount(container: HTMLElement) {
    const root: Root = createRoot(container);
    root.render(<ComingSoon />);
    return () => root.unmount();
  },
};
