"use client";

import { useId, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";

/** A row that opens the less-used settings under it. `count` says how many of them differ from the default. */
export function Disclosure({ label, count, open, onOpenChange, children, className, ...rest }: {
  label: string;
  count?: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
  className?: string;
  "data-tour"?: string;
}) {
  const id = useId();
  return (
    <div className={className} {...rest}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => onOpenChange(!open)}
        className="-mx-2.5 flex w-[calc(100%+1.25rem)] items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="min-w-0 flex-1 truncate">{label}</span>
        {Boolean(count) && <span className="shrink-0 rounded-full bg-foreground px-1.5 text-[10px] font-medium text-background">{count}</span>}
        <ChevronDown className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} aria-hidden />
      </button>
      {open && <div id={id} className="pt-2">{children}</div>}
    </div>
  );
}
