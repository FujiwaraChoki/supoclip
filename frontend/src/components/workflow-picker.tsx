"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { isWorkflow, WORKFLOWS, type Workflow, type WorkflowsInfo } from "@/lib/workflows";
import { cn } from "@/lib/utils";

const UNAVAILABLE: WorkflowsInfo = { enabled: false, workflows: [], topic_max_chars: 0, instructions_max_chars: 0 };

/**
 * `GET /tasks/workflows` for the signed-in user: null while loading, and
 * disabled when the request fails, so the create screen falls back to plain
 * highlights.
 */
export function useWorkflowsInfo(active = true): WorkflowsInfo | null {
  const [info, setInfo] = useState<WorkflowsInfo | null>(null);
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    fetch("/api/tasks/workflows", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((data: Partial<WorkflowsInfo> | null) => {
        if (cancelled) return;
        setInfo(data && Array.isArray(data.workflows) ? { ...UNAVAILABLE, ...data, enabled: data.enabled === true } as WorkflowsInfo : UNAVAILABLE);
      })
      .catch(() => { if (!cancelled) setInfo(UNAVAILABLE); });
    return () => { cancelled = true; };
  }, [active]);
  return info;
}

/** Workflows the user may pick right now. Empty unless the server explicitly enables them. */
export function availableWorkflows(info: WorkflowsInfo | null): ReadonlySet<Workflow> {
  if (info?.enabled !== true) return new Set();
  return new Set(info.workflows.filter((entry) => entry.available === true && isWorkflow(entry.id)).map((entry) => entry.id));
}

/** "What do you want to make?" as five option cards: one radio group with arrow-key navigation. */
export function WorkflowPicker({ value, onChange, disabled, available, labelId }: {
  value: Workflow;
  onChange: (workflow: Workflow) => void;
  disabled?: boolean;
  available: ReadonlySet<Workflow>;
  labelId?: string;
}) {
  const t = useTranslations("home");
  const refs = useRef(new Map<Workflow, HTMLButtonElement>());
  const options = WORKFLOWS.filter((id) => available.has(id));
  const focusable = options.includes(value) ? value : options[0];

  const move = (from: Workflow, step: number) => {
    const index = options.indexOf(from);
    const next = options[(index + step + options.length) % options.length];
    if (!next) return;
    onChange(next);
    refs.current.get(next)?.focus();
  };

  return (
    <div role="radiogroup" aria-labelledby={labelId} aria-disabled={disabled || undefined} className="grid grid-cols-2 gap-2 sm:grid-cols-5">
      {WORKFLOWS.map((id) => {
        const selectable = available.has(id);
        const checked = value === id;
        return (
          <button
            key={id}
            ref={(node) => { if (node) refs.current.set(id, node); else refs.current.delete(id); }}
            type="button"
            role="radio"
            aria-checked={checked}
            disabled={disabled || !selectable}
            tabIndex={id === focusable ? 0 : -1}
            onClick={() => onChange(id)}
            onKeyDown={(event) => {
              const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
              if (step) { event.preventDefault(); move(id, step); return; }
              if (event.key === "Home" || event.key === "End") {
                event.preventDefault();
                const target = event.key === "Home" ? options[0] : options[options.length - 1];
                if (target) { onChange(target); refs.current.get(target)?.focus(); }
              }
            }}
            className={cn(
              "flex flex-col rounded-xl border bg-background p-3 text-left transition-colors last:col-span-2 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 sm:last:col-span-1",
              checked && "border-foreground ring-1 ring-foreground",
            )}
          >
            <WorkflowGlyph id={id} />
            <span className="mt-2 block break-words text-sm font-medium">{t(`workflows.${id}.label`)}</span>
            <span className="block break-words text-xs text-muted-foreground">{t(`workflows.${id}.hint`)}</span>
          </button>
        );
      })}
    </div>
  );
}

/** Monochrome sketch of what each workflow makes, in the style of the framing glyphs. */
export function WorkflowGlyph({ id, className }: { id: Workflow; className?: string }) {
  return (
    <span className={cn("flex h-12 items-center justify-center rounded-lg bg-muted", className)} aria-hidden>
      {id === "highlights" ? (
        // Standalone moments picked from across the video; the best one marked.
        <span className="flex h-8 items-end gap-1.5">
          <span className="h-3 w-1.5 rounded-full bg-foreground/30" />
          <span className="h-5 w-1.5 rounded-full bg-foreground/30" />
          <span className="relative h-7 w-1.5 rounded-full bg-foreground/70">
            <span className="absolute -top-2.5 left-1/2 size-1.5 -translate-x-1/2 rounded-full bg-brand" />
          </span>
          <span className="h-4 w-1.5 rounded-full bg-foreground/30" />
          <span className="h-6 w-1.5 rounded-full bg-foreground/30" />
        </span>
      ) : id === "series" ? (
        // Consecutive parts that butt against each other.
        <span className="flex h-7 overflow-hidden rounded-[4px] border-2 border-foreground/70 text-[9px] font-semibold leading-none text-foreground/70">
          {[1, 2, 3].map((part) => (
            <span key={part} className="flex w-5 items-center justify-center border-foreground/70 [&:not(:first-child)]:border-l-2">{part}</span>
          ))}
        </span>
      ) : id === "topic" ? (
        // Only the moments about one subject light up.
        <span className="relative flex h-8 items-end gap-1.5">
          <span className="h-4 w-1.5 rounded-full bg-foreground/15" />
          <span className="h-6 w-1.5 rounded-full bg-foreground/70" />
          <span className="h-3 w-1.5 rounded-full bg-foreground/15" />
          <span className="h-5 w-1.5 rounded-full bg-foreground/15" />
          <span className="h-7 w-1.5 rounded-full bg-foreground/70" />
          <span className="absolute -right-3 -top-1 size-3 rounded-full border-2 border-foreground/70" />
        </span>
      ) : id === "recap" ? (
        // Several points funnel into one video.
        <svg viewBox="0 0 40 28" className="h-7 w-10 text-foreground/70" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M3 4h8M3 10.5h8M3 17.5h8M3 24h8" strokeOpacity="0.45" />
          <path d="M13 4l9 10M13 10.5l9 3.5M13 17.5l9-3.5M13 24l9-10" strokeOpacity="0.45" strokeWidth="1.5" />
          <path d="M24 14h13" strokeWidth="3" />
        </svg>
      ) : (
        // A short preview: play, then a few quick beats.
        <svg viewBox="0 0 40 28" className="h-7 w-10 text-foreground/70" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M5 6.5v15l12-7.5z" fill="currentColor" fillOpacity="0.15" />
          <path d="M24 9.5v9M30 7v14M36 11v6" strokeOpacity="0.45" />
        </svg>
      )}
    </span>
  );
}
