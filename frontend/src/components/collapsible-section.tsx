"use client";

import type { ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";

interface CollapsibleSectionProps {
  /** Used to wire the trigger to its panel for screen readers. */
  id: string;
  title: string;
  /** Shown under the trigger while the section is closed. */
  summary?: string;
  icon?: ReactNode;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
}

export function CollapsibleSection({
  id,
  title,
  summary,
  icon,
  open,
  onOpenChange,
  children,
}: CollapsibleSectionProps) {
  return (
    <Card className="border-stone-200">
      <CardContent className="px-4 pt-0 pb-4 space-y-3">
        <button
          type="button"
          id={`${id}-trigger`}
          aria-expanded={open}
          aria-controls={`${id}-panel`}
          onClick={() => onOpenChange(!open)}
          className="flex w-full items-center justify-between gap-3 text-left cursor-pointer"
        >
          <span className="flex items-center gap-2 text-sm font-medium text-stone-900">
            {icon}
            {title}
          </span>
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            {open ? "Hide" : "Show"}
            <ChevronDown className={`w-4 h-4 transition-transform ${open ? "rotate-180" : ""}`} />
          </span>
        </button>

        {summary && !open && <p className="text-xs text-muted-foreground">{summary}</p>}

        {open && (
          <div id={`${id}-panel`} role="region" aria-labelledby={`${id}-trigger`} className="space-y-4 pt-1">
            {children}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
