import { Scissors } from "lucide-react";

import type { Tool } from "../types";

/**
 * The original (and so far only) tool: long-form video in, short clips out.
 * Its actual implementation is the existing route subtree at
 * frontend/src/app/(clipping)/ — create, list, tasks/[id], trash — unchanged
 * from before this file existed. This descriptor is the thin registration
 * point for the tab shell, not a reimplementation; see Tool's doc-comment
 * for why a route-owning tool uses `href` instead of `mount`.
 */
export const clippingTool: Tool = {
  id: "clipping",
  name: "Clipping",
  icon: Scissors,
  description: "Turn long-form video into short clips",
  thumbnail: "/assets/tools/clipping.svg",
  href: "/list",
  matchPaths: ["/create", "/tasks", "/trash", "/batch"],
};
