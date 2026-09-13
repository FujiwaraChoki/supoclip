"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { TOOLS } from "@/tools/registry";
import { cn } from "@/lib/utils";

/**
 * Top-level tool switcher — the platform "shell" every tool's screens sit
 * under. A route-owning tool (has `href`) is active whenever the current
 * path falls under it; a mount-based tool is active by exact path match
 * (it only ever has the one page hosting it).
 */
export function ToolTabs() {
  const pathname = usePathname();

  return (
    <nav className="border-b border-border bg-background">
      <div className="max-w-6xl mx-auto px-4 flex items-center gap-1">
        {TOOLS.map((tool) => {
          const href = tool.href ?? `/tools/${tool.id}`;
          const matchPaths = [href, ...(tool.matchPaths ?? [])];
          const isActive = matchPaths.some(
            (path) => pathname === path || pathname.startsWith(`${path}/`),
          );
          return (
            <Link
              key={tool.id}
              href={href}
              className={cn(
                "flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 -mb-px transition-colors",
                isActive
                  ? "border-foreground text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              <tool.icon className="w-4 h-4" />
              {tool.name}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
