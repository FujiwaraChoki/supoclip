import { noIndexMetadata } from "@/lib/seo";
import { ToolTabs } from "@/components/tool-tabs";

export const metadata = noIndexMetadata;

// Shared shell for every Ranking-tool route (create, tasks/[id]) — a route
// group ("(rank)"), so it changes none of their URLs. Mirrors
// (clipping)/layout.tsx exactly.
export default function RankingLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <ToolTabs />
      {children}
    </>
  );
}
