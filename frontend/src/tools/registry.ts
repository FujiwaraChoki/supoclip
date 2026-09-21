import type { Tool } from "./types";
import { clippingTool } from "./clipping";
import { rankingTool } from "./ranking";

/**
 * Every tool on the platform, in tab display order. No dynamic loading, no
 * plugin discovery — just a literal list.
 *
 * To add a new tool later:
 *   1. Create `frontend/src/tools/<your-tool>/index.ts(x)` exporting a
 *      `Tool` (see types.ts) — `href` if it's its own route subtree under
 *      `frontend/src/app/`, `mount` if it's simple enough to render
 *      straight into a container (see `placeholder/index.tsx`, still
 *      available as a reference pattern at /tools/placeholder though no
 *      longer registered here).
 *   2. If it's route-based, add the route(s) under `frontend/src/app/`
 *      (its own route group, e.g. `(rank)/` for Ranking, keeps it visually
 *      separate from `(clipping)/` the same way that group is separate
 *      today — see CLAUDE.md's "Adding a new tool" section).
 *   3. Add it to this array.
 * That's the whole integration surface — nothing else in the shell needs
 * to change.
 */
export const TOOLS: Tool[] = [clippingTool, rankingTool];
