import { ListOrdered } from "lucide-react";

import type { Tool } from "../types";

/**
 * The second real tool on the platform (replacing the generic Placeholder
 * tab): multiple short input videos in, one ranked compilation video out —
 * the inverse cardinality of Clipping's one-source-to-many-clips pipeline.
 * Route-owning (its own route group, `frontend/src/app/(rank)/`) since it
 * spans more than one page (input/ordering, then the render/result view),
 * the same shape as Clipping.
 */
export const rankingTool: Tool = {
  id: "ranking",
  name: "Ranking",
  icon: ListOrdered,
  description: "Rank clips into one compilation video",
  thumbnail: "/assets/tools/ranking.svg",
  href: "/rank/create",
  matchPaths: ["/rank/tasks"],
};
