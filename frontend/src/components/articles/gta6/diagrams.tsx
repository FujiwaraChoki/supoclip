import { Captions, CircleCheck, Crop, Download, Film, ListChecks, Scissors, ShieldAlert, TriangleAlert, CircleX } from "lucide-react";

import { getGta6Copy, type Gta6RuleStatus, type GuideLocale } from "@/lib/localized-guides";

const STEP_ICONS = [Film, ListChecks, Crop, Scissors, Captions, Download] as const;

/** The six-step workflow: what you decide versus what SupoClip automates. */
export function WorkflowDiagram({ locale = "en" }: { locale?: GuideLocale }) {
  const copy = getGta6Copy(locale).workflowDiagram;

  return (
    <figure className="not-prose space-y-4" aria-labelledby="workflow-diagram-title">
      <figcaption id="workflow-diagram-title" className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">{copy.caption}</figcaption>
      <div role="list" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {copy.steps.map(({ title, you, app }, index) => {
          const Icon = STEP_ICONS[index] ?? Film;
          return (
            <div role="listitem" key={title} className="relative rounded-xl border bg-background p-4">
              <div className="flex items-center gap-3">
                <span className="flex size-8 items-center justify-center rounded-lg bg-foreground font-mono text-sm font-bold text-background">{index + 1}</span>
                <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
                <span className="font-semibold text-foreground">{title}</span>
              </div>
              <dl className="mt-3 space-y-2 text-sm leading-5">
                <div><dt className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{copy.youLabel}</dt><dd className="text-foreground">{you}</dd></div>
                <div><dt className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{copy.appLabel}</dt><dd className="text-muted-foreground">{app}</dd></div>
              </dl>
            </div>
          );
        })}
      </div>
    </figure>
  );
}

/** 16:9 stream with a corner webcam becomes a 1080×1920 clip: reactor 640px over 1280px of gameplay. */
export function ReactionLayoutFigure({ locale = "en" }: { locale?: GuideLocale }) {
  const copy = getGta6Copy(locale).reactionLayout;

  return (
    <figure className="not-prose space-y-3 rounded-2xl border bg-muted/20 p-5 sm:p-7">
      <svg viewBox="0 0 820 400" role="img" aria-labelledby="reaction-layout-title reaction-layout-desc" className="h-auto w-full">
        <title id="reaction-layout-title">{copy.svgTitle}</title>
        <desc id="reaction-layout-desc">{copy.svgDesc}</desc>
        <defs>
          <linearGradient id="rl-sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#3b1d6e" /><stop offset=".6" stopColor="#d9447a" /><stop offset="1" stopColor="#ff9a4c" /></linearGradient>
        </defs>
        {/* source 16:9 */}
        <g transform="translate(10 80)">
          <rect width="400" height="225" rx="10" fill="url(#rl-sky)" />
          <g fill="#2a1240"><rect x="20" y="110" width="34" height="115" /><rect x="60" y="90" width="28" height="135" /><rect x="300" y="100" width="40" height="125" /><rect x="346" y="130" width="34" height="95" /></g>
          <circle cx="210" cy="160" r="40" fill="#ffe3a1" opacity=".9" />
          <rect x="300" y="10" width="90" height="62" rx="6" fill="#2b2140" stroke="#ffffff" strokeWidth="3" />
          <circle cx="345" cy="36" r="13" fill="#f2b38a" /><path d="M322 72 q23 -32 46 0z" fill="#5a3fd1" />
          <text x="0" y="252" className="fill-foreground" fontSize="15" fontWeight="700">{copy.sourceTitle}</text>
          <text x="0" y="272" className="fill-muted-foreground" fontSize="13">{copy.sourceSubtitle}</text>
        </g>
        {/* arrow */}
        <g className="stroke-muted-foreground" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round">
          <path d="M428 192 H512" strokeDasharray="2 9" /><path d="M500 180 L514 192 L500 204" />
        </g>
        {/* output 9:16 */}
        <g transform="translate(540 10)">
          <rect width="200" height="356" rx="14" className="fill-background stroke-border" strokeWidth="2" />
          <svg x="0" y="0" width="200" height="356"><rect width="200" height="119" fill="#2b2140" /><circle cx="100" cy="50" r="26" fill="#f2b38a" /><path d="M52 119 q48 -64 96 0z" fill="#5a3fd1" />
            <rect y="119" width="200" height="237" fill="url(#rl-sky)" /><g fill="#2a1240"><rect x="6" y="230" width="40" height="126" /><rect x="150" y="215" width="44" height="141" /></g><circle cx="100" cy="270" r="38" fill="#ffe3a1" opacity=".9" />
            <rect x="24" y="312" width="152" height="24" rx="5" fill="#0b0712" opacity=".85" /><text x="100" y="329" textAnchor="middle" fill="#ffffff" fontSize="11" fontWeight="800">{copy.captionsHere}</text></svg>
          <rect width="200" height="356" rx="14" fill="none" className="stroke-foreground" strokeWidth="2" />
          <line x1="0" y1="119" x2="200" y2="119" stroke="#ffffff" strokeWidth="2" strokeDasharray="4 4" />
          <text x="212" y="64" className="fill-muted-foreground" fontSize="12">640 px</text>
          <text x="212" y="244" className="fill-muted-foreground" fontSize="12">1280 px</text>
          <text x="0" y="384" className="fill-foreground" fontSize="15" fontWeight="700">{copy.outputTitle}</text>
        </g>
      </svg>
      <figcaption className="text-sm leading-6 text-muted-foreground">{copy.figcaption}</figcaption>
    </figure>
  );
}

const ANATOMY_END = 43;

/** Proportional bar of a 43-second clip. Illustrative structure, not a rule. */
export function ClipAnatomy({ locale = "en" }: { locale?: GuideLocale }) {
  const copy = getGta6Copy(locale).clipAnatomy;

  return (
    <figure className="not-prose space-y-4 rounded-2xl border bg-muted/20 p-5 sm:p-7" aria-labelledby="clip-anatomy-title">
      <div>
        <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">{copy.eyebrow}</p>
        <h3 id="clip-anatomy-title" className="mt-1 text-xl font-bold tracking-tight text-foreground">{copy.title}</h3>
      </div>
      <div className="relative">
        <div className="flex h-10 gap-0.5 overflow-hidden rounded-lg" aria-hidden="true">
          {copy.parts.map((part, index) => (
            <div key={part.label} style={{ flexGrow: part.to - part.from }}
              className={`flex items-center justify-center text-[11px] font-semibold ${index === 0 || index === 3 ? "bg-foreground text-background" : "bg-muted text-foreground"}`}>
              <span className="truncate px-1">{part.label}</span>
            </div>
          ))}
        </div>
        <div className="mt-1 flex h-5 overflow-hidden rounded" aria-hidden="true">
          <div style={{ width: `${(4 / ANATOMY_END) * 100}%` }} className="rounded bg-[repeating-linear-gradient(135deg,var(--muted-foreground)_0_2px,transparent_2px_6px)] opacity-60" />
          <span className="ml-2 text-[11px] leading-5 text-muted-foreground">{copy.hookBarNote}</span>
        </div>
        <div className="mt-1 flex justify-between font-mono text-[11px] text-muted-foreground" aria-hidden="true"><span>0 {copy.secondsUnit}</span><span>{ANATOMY_END} {copy.secondsUnit}</span></div>
      </div>
      <div role="list" className="grid gap-x-6 gap-y-2 text-sm leading-6 sm:grid-cols-2">
        {copy.parts.map((part) => (
          <div role="listitem" key={part.label}><span className="font-semibold text-foreground">{part.label}</span> <span className="font-mono text-xs text-muted-foreground">{part.from}–{part.to} {copy.secondsUnit}</span> · <span className="text-muted-foreground">{part.note}</span></div>
        ))}
      </div>
    </figure>
  );
}

const RULE_ICONS: Record<Gta6RuleStatus, typeof CircleCheck> = {
  ok: CircleCheck,
  warn: TriangleAlert,
  no: CircleX,
};

const RULE_TONE: Record<Gta6RuleStatus, string> = {
  ok: "text-green-700 dark:text-green-400",
  warn: "text-amber-700 dark:text-amber-400",
  no: "text-red-700 dark:text-red-400",
};

export function RulesBoard({ locale = "en" }: { locale?: GuideLocale }) {
  const copy = getGta6Copy(locale).rulesBoard;

  return (
    <figure className="not-prose space-y-3" aria-labelledby="rules-board-title">
      <figcaption id="rules-board-title" className="flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-muted-foreground"><ShieldAlert className="size-4" aria-hidden="true" />{copy.caption}</figcaption>
      <div className="grid gap-3 md:grid-cols-3">
        {copy.groups.map(({ status, label, items }) => {
          const Icon = RULE_ICONS[status];
          return (
            <section key={status} className="rounded-xl border bg-background p-4" aria-label={label}>
              <p className={`flex items-center gap-2 font-semibold ${RULE_TONE[status]}`}><Icon className="size-4" aria-hidden="true" />{label}</p>
              <ul className="mt-3 space-y-2 text-sm leading-5 text-muted-foreground" style={{ marginLeft: "1rem" }}>{items.map((item) => <li key={item}>{item}</li>)}</ul>
            </section>
          );
        })}
      </div>
    </figure>
  );
}

export function LaunchWeekTable({ locale = "en" }: { locale?: GuideLocale }) {
  const copy = getGta6Copy(locale).launchWeekTable;

  return (
    <div className="not-prose overflow-x-auto rounded-xl border">
      <table className="w-full min-w-[560px] text-left text-sm leading-6">
        <caption className="border-b px-4 py-3 text-left font-medium text-foreground">{copy.caption}</caption>
        <thead className="bg-muted/50"><tr>{copy.headers.map((header) => <th key={header} scope="col" className="p-4">{header}</th>)}</tr></thead>
        <tbody>{copy.rows.map(([when, focus, what]) => (
          <tr key={when} className="border-t"><th scope="row" className="whitespace-nowrap p-4 align-top font-mono text-xs">{when}</th><td className="p-4 align-top font-medium text-foreground">{focus}</td><td className="p-4 align-top text-muted-foreground">{what}</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}
