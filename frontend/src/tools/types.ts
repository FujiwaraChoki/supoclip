import type { LucideIcon } from "lucide-react";

/**
 * Minimal shape every tool on the platform is described by — the "plug-in
 * point" for turning SupoClip from a single-purpose clipper into a
 * multi-tool platform. Deliberately small: no plugin registry, no dynamic
 * loading, no dependency injection. A new tool is just one more object
 * matching this shape, added to `registry.ts` — see that file's comment for
 * the concrete steps.
 *
 * `mount`/`cleanup` are the generic contract every tool exposes, but how a
 * given tool actually implements `mount` depends on its shape:
 *   - A self-contained tool with no sub-navigation (the Placeholder tool
 *     below) can genuinely render its own UI straight into `container`.
 *   - A tool built as its own Next.js route subtree (Clipping — trim/split/
 *     export/etc. are all separate pages with their own routing, not one
 *     screen) "mounts" by owning that subtree: its `mount` navigates into
 *     the tool's entry route rather than rendering DOM itself, since Next's
 *     App Router already owns rendering for anything under `app/`. This is
 *     the expected pattern for any future tool substantial enough to need
 *     multiple pages (the ranking/compilation and voiceover tools described
 *     in planning almost certainly will) — see ToolMount's dispatch on
 *     `href` vs `mount` in tool-mount.tsx.
 */
export interface Tool {
  id: string;
  name: string;
  icon: LucideIcon;
  /** One-line description shown in the tab bar / coming-soon state. */
  description: string;
  /**
   * Path (under `public/`) to a static SVG thumbnail for this tool's card on
   * the home screen's Tools grid, e.g. "/assets/tools/clipping.svg". Kept as
   * a plain file reference (not inlined/base64'd) so art can be swapped
   * without touching code. Optional — ToolCard (home/tools-grid.tsx) falls
   * back to a CSS-generated icon tile using `icon`/`name` if this is absent
   * or fails to load.
   */
  thumbnail?: string;
  /**
   * Mounts the tool's UI into `container`. Returns an optional cleanup
   * function (or void), called when the tool is unmounted/switched away
   * from. Route-owning tools (see class comment) may leave this out and
   * rely on `href` instead — ToolMount handles both.
   */
  mount?: (container: HTMLElement) => (() => void) | void;
  /** For a route-owning tool: the Next.js route this tool's UI lives under
   * (used as both the tab's link target and its "active" match). */
  href?: string;
  /** For a route-owning tool spanning more than one top-level route (like
   * Clipping's create/list/tasks/trash): the additional path prefixes that
   * also count as "inside" this tool, so the tab bar highlights correctly
   * on all of them, not just `href`. */
  matchPaths?: string[];
}
