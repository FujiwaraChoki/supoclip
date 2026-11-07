"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
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
    <div data-tour="workflow-picker" role="radiogroup" aria-labelledby={labelId} aria-disabled={disabled || undefined} className="-mx-4 flex rounded-2xl snap-x snap-mandatory scroll-px-4 gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:grid sm:grid-cols-5 sm:overflow-visible sm:px-0 sm:pb-0">
      {WORKFLOWS.map((id) => {
        const selectable = available.has(id);
        const checked = value === id;
        return (
          <button
            key={id}
            data-tour={`workflow-${id}`}
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
              "group/workflow flex w-40 shrink-0 snap-start flex-col rounded-xl border bg-background p-3 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 sm:w-auto",
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

/** A vertical clip: the shape every workflow's output takes. `strong` marks the one the card is about. */
function Tile({ x, y = 6, w = 18, h = 32, strong, children }: { x: number; y?: number; w?: number; h?: number; strong?: boolean; children?: ReactNode }) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx="3.5" fill="var(--background)" stroke="currentColor" strokeOpacity={strong ? 0.75 : 0.3} strokeWidth="1.25" />
      {children ?? <rect x={x + 4} y={y + h - 8} width={w - 8} height="2.5" rx="1.25" fill="currentColor" fillOpacity={strong ? 0.6 : 0.25} />}
    </g>
  );
}

/** The source video along the bottom, with the parts each workflow uses filled in. */
function Source({ marks }: { marks: readonly (readonly [number, number])[] }) {
  return (
    <g>
      <rect x="8" y="58" width="104" height="7" rx="3.5" fill="currentColor" fillOpacity="0.12" />
      {marks.map(([x, width]) => <rect key={x} x={x} y="58" width={width} height="7" rx={Math.min(width / 2, 3.5)} fill="currentColor" fillOpacity="0.7" />)}
    </g>
  );
}

function Link({ from, to, y = 38 }: { from: number; to: number; y?: number }) {
  return <path d={`M${from} ${y}C${from} 49 ${to} 47 ${to} 57`} stroke="currentColor" strokeOpacity="0.28" strokeWidth="1" fill="none" />;
}

/** Colored only on the selected card, so one accent shows at a time. */
const ACCENT = "text-foreground/70 group-aria-checked/workflow:text-brand";

/** What each workflow gives you from one video: the source along the bottom, the vertical clips it becomes on top. */
export function WorkflowGlyph({ id, className }: { id: Workflow; className?: string }) {
  return (
    <span className={cn("flex items-center justify-center rounded-lg bg-muted px-2 py-1.5", className)} aria-hidden>
      <svg viewBox="0 0 120 70" className="h-[4.5rem] w-full max-w-36 text-foreground">
        {id === "highlights" ? (
          // A few standalone moments from anywhere in the video, the best one marked.
          <>
            <Link from={31} to={21} /><Link from={60} to={66} /><Link from={89} to={96} />
            <Source marks={[[15, 12], [59, 14], [90, 11]]} />
            <Tile x={22} /><Tile x={51} strong /><Tile x={80} />
            <circle cx="69" cy="6" r="4" fill="currentColor" className={ACCENT} stroke="var(--muted)" strokeWidth="1.5" />
          </>
        ) : id === "series" ? (
          // The whole video, cut end to end into numbered parts that follow on.
          <>
            <Link from={25} to={25} /><Link from={60} to={60} /><Link from={95} to={95} />
            <Source marks={[[8, 33.5], [43.25, 33.5], [78.5, 33.5]]} />
            {[16, 51, 86].map((x, index) => (
              <Tile key={x} x={x} strong>
                <text x={x + 9} y="26" textAnchor="middle" fontSize="11" fontWeight="600" fill="currentColor" fillOpacity="0.75">{index + 1}</text>
              </Tile>
            ))}
            <g className={ACCENT} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M38.5 19l3 3-3 3" /><path d="M73.5 19l3 3-3 3" />
            </g>
          </>
        ) : id === "topic" ? (
          // A search picks out only the moments about one subject.
          <>
            <Link from={51} to={38} /><Link from={75} to={69} /><Link from={99} to={102} />
            <Source marks={[[33, 10], [64, 10], [97, 10]]} />
            <g className={ACCENT} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <circle cx="17" cy="17" r="7" /><path d="M22.5 22.5l5.5 5.5" />
            </g>
            <Tile x={42} strong /><Tile x={66} strong /><Tile x={90} strong />
          </>
        ) : id === "recap" ? (
          // Points from across the whole video, gathered into one clip that lists them.
          <>
            <Link from={50} to={14} y={40} /><Link from={55} to={37} y={40} /><Link from={60} to={60} y={40} /><Link from={65} to={83} y={40} /><Link from={70} to={106} y={40} />
            <Source marks={[[10, 8], [33, 8], [56, 8], [79, 8], [102, 8]]} />
            <Tile x={46} y={2} w={28} h={38} strong>
              {[10, 18, 26].map((y, index) => (
                <g key={y}>
                  <circle cx="52" cy={y} r="1.75" fill="currentColor" className={index === 0 ? ACCENT : undefined} fillOpacity={index === 0 ? 1 : 0.55} />
                  <rect x="56.5" y={y - 1.25} width={index === 1 ? 9 : 12} height="2.5" rx="1.25" fill="currentColor" fillOpacity="0.45" />
                </g>
              ))}
            </Tile>
          </>
        ) : (
          // Quick cuts make one teaser that points back to the full video.
          <>
            <Link from={37} to={15} /><Link from={41} to={38} /><Link from={45} to={58} /><Link from={49} to={84} />
            <Source marks={[[13, 4], [36, 4], [56, 4], [82, 4]]} />
            <Tile x={32} y={2} w={22} h={36} strong>
              <path d="M39 14.5v13l10-6.5z" fill="currentColor" className={ACCENT} />
            </Tile>
            <path d="M60 20h9m-3-3l3 3-3 3" fill="none" stroke="currentColor" strokeOpacity="0.45" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" />
            <rect x="74" y="10" width="34" height="20" rx="3.5" fill="var(--background)" stroke="currentColor" strokeOpacity="0.3" strokeWidth="1.25" />
            <path d="M88.5 16v8l6-4z" fill="currentColor" fillOpacity="0.35" />
          </>
        )}
      </svg>
    </span>
  );
}
