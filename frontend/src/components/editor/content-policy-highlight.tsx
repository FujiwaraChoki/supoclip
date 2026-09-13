"use client";

import type { ReactNode } from "react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface ContentPolicyFlag {
  word: string;
  category: string;
  start: number;
  end: number;
  severity: "severe" | "borderline";
  source: "regex" | "llm";
}

interface ContentPolicyHighlightProps {
  text: string;
  flags: ContentPolicyFlag[];
  className?: string;
}

/** Renders `text` with every flagged span wrapped in a highlighted `<mark>`
 * and a tooltip naming its category — a preview of what will be asterisked
 * on export, without mutating the underlying caption text. */
export function ContentPolicyHighlight({ text, flags, className }: ContentPolicyHighlightProps) {
  if (!flags.length) {
    return <span className={className}>{text}</span>;
  }

  const sorted = [...flags].sort((a, b) => a.start - b.start);
  const nodes: ReactNode[] = [];
  let cursor = 0;

  sorted.forEach((flag, idx) => {
    if (flag.start > cursor) {
      nodes.push(<span key={`plain-${idx}`}>{text.slice(cursor, flag.start)}</span>);
    }
    nodes.push(
      <Tooltip key={`flag-${idx}`}>
        <TooltipTrigger asChild>
          <mark
            className={
              flag.severity === "severe"
                ? "bg-foreground text-background px-0.5"
                : "bg-transparent underline decoration-dashed decoration-foreground underline-offset-2"
            }
          >
            {text.slice(flag.start, flag.end)}
          </mark>
        </TooltipTrigger>
        <TooltipContent>
          {flag.category} ({flag.severity}
          {flag.source === "llm" ? ", AI-detected" : ""})
        </TooltipContent>
      </Tooltip>
    );
    cursor = Math.max(cursor, flag.end);
  });

  if (cursor < text.length) {
    nodes.push(<span key="plain-end">{text.slice(cursor)}</span>);
  }

  return <span className={className}>{nodes}</span>;
}
