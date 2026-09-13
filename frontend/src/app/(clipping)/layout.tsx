import { noIndexMetadata } from "@/lib/seo";
import { ToolTabs } from "@/components/tool-tabs";

export const metadata = noIndexMetadata;

// Shared shell for every Clipping-tool route (create/list/tasks/trash) — a
// route group ("(clipping)"), so it changes none of their URLs. Consolidates
// what were three near-identical per-route layout.tsx files (list, tasks;
// create/trash had none) into one, and adds the tool tab bar described in
// CLAUDE.md's tools/ section.
export default function ClippingLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <ToolTabs />
      {children}
    </>
  );
}
