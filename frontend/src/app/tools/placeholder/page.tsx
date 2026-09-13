import { ToolTabs } from "@/components/tool-tabs";
import { ToolMount } from "@/components/tool-mount";
import { placeholderTool } from "@/tools/placeholder";
import { noIndexMetadata } from "@/lib/seo";

export const metadata = noIndexMetadata;

// Route for the one generic "coming soon" tool — see tools/registry.ts for
// how a real future tool would replace/join this.
export default function PlaceholderToolPage() {
  return (
    <div className="min-h-screen bg-background">
      <ToolTabs />
      <ToolMount tool={placeholderTool} />
    </div>
  );
}
