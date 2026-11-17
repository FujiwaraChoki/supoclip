import { Captions, CircleCheck, Crop, Download, Film, ListChecks, Scissors, ShieldAlert, TriangleAlert, CircleX } from "lucide-react";

const STEPS = [
  { Icon: Film, title: "Pick a VOD", you: "Your own stream, or one a campaign lets you clip", app: "Imports Twitch, Kick and YouTube links or a file" },
  { Icon: ListChecks, title: "Shortlist", you: "Watch candidates, note silent plays", app: "Transcribes speech and ranks spoken moments" },
  { Icon: Crop, title: "Frame", you: "Check every scene change at phone size", app: "Stacks a corner webcam above the gameplay" },
  { Icon: Scissors, title: "Cut", you: "Hook in the first seconds, end on the payoff", app: "Trim, split, and AI edits in the studio" },
  { Icon: Captions, title: "Caption", you: "Fix names: Lucia, Jason, Leonida, Vice City", app: "Word-synced captions and a hook title" },
  { Icon: Download, title: "Publish", you: "Submit links to the campaign, log results", app: "Exports 9:16, posts to TikTok and YouTube" },
] as const;

/** The six-step workflow: what you decide versus what SupoClip automates. */
export function WorkflowDiagram() {
  return (
    <figure className="not-prose space-y-4" aria-labelledby="workflow-diagram-title">
      <figcaption id="workflow-diagram-title" className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">The GTA 6 clipping workflow · you decide, SupoClip does the repetitive part</figcaption>
      <div role="list" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {STEPS.map(({ Icon, title, you, app }, index) => (
          <div role="listitem" key={title} className="relative rounded-xl border bg-background p-4">
            <div className="flex items-center gap-3">
              <span className="flex size-8 items-center justify-center rounded-lg bg-foreground font-mono text-sm font-bold text-background">{index + 1}</span>
              <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
              <span className="font-semibold text-foreground">{title}</span>
            </div>
            <dl className="mt-3 space-y-2 text-sm leading-5">
              <div><dt className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">You</dt><dd className="text-foreground">{you}</dd></div>
              <div><dt className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">SupoClip</dt><dd className="text-muted-foreground">{app}</dd></div>
            </dl>
          </div>
        ))}
      </div>
    </figure>
  );
}

/** 16:9 stream with a corner webcam becomes a 1080×1920 clip: reactor 640px over 1280px of gameplay. */
export function ReactionLayoutFigure() {
  return (
    <figure className="not-prose space-y-3 rounded-2xl border bg-muted/20 p-5 sm:p-7">
      <svg viewBox="0 0 820 400" role="img" aria-labelledby="reaction-layout-title reaction-layout-desc" className="h-auto w-full">
        <title id="reaction-layout-title">From a 16:9 stream to a stacked 9:16 clip</title>
        <desc id="reaction-layout-desc">On the left, a widescreen stream with the webcam in the top-right corner. On the right, the vertical clip: the webcam fills the top third and the gameplay fills the bottom two thirds.</desc>
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
          <text x="0" y="252" className="fill-foreground" fontSize="15" fontWeight="700">Stream VOD · 16:9</text>
          <text x="0" y="272" className="fill-muted-foreground" fontSize="13">Webcam fixed in an upper corner</text>
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
            <rect x="34" y="312" width="132" height="24" rx="5" fill="#0b0712" opacity=".85" /><text x="100" y="329" textAnchor="middle" fill="#ffffff" fontSize="12" fontWeight="800">captions sit here</text></svg>
          <rect width="200" height="356" rx="14" fill="none" className="stroke-foreground" strokeWidth="2" />
          <line x1="0" y1="119" x2="200" y2="119" stroke="#ffffff" strokeWidth="2" strokeDasharray="4 4" />
          <text x="212" y="64" className="fill-muted-foreground" fontSize="12">640 px</text>
          <text x="212" y="244" className="fill-muted-foreground" fontSize="12">1280 px</text>
          <text x="0" y="384" className="fill-foreground" fontSize="15" fontWeight="700">Clip · 9:16 · 1080×1920</text>
        </g>
      </svg>
      <figcaption className="text-sm leading-6 text-muted-foreground">When SupoClip finds a steady face in an upper corner of the frame, it places the streamer in the top third and the gameplay underneath. No steady webcam, or a webcam that moves? It falls back to its other layouts, so watch the result.</figcaption>
    </figure>
  );
}

const ANATOMY = [
  { label: "Hook", from: 0, to: 3, note: "Say or show the situation: “six stars, one minute in”." },
  { label: "Context", from: 3, to: 10, note: "Only what a stranger needs to follow it." },
  { label: "Build", from: 10, to: 32, note: "The chase, the heist, the argument with chat." },
  { label: "Payoff", from: 32, to: 40, note: "The crash, the escape, the reaction." },
  { label: "Out", from: 40, to: 43, note: "Stop. No “anyway, so…”" },
] as const;
const ANATOMY_END = 43;

/** Proportional bar of a 43-second clip. Illustrative structure, not a rule. */
export function ClipAnatomy() {
  return (
    <figure className="not-prose space-y-4 rounded-2xl border bg-muted/20 p-5 sm:p-7" aria-labelledby="clip-anatomy-title">
      <div>
        <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Anatomy of one clip</p>
        <h3 id="clip-anatomy-title" className="mt-1 text-xl font-bold tracking-tight text-foreground">A 43-second GTA 6 chase, cut to hold attention</h3>
      </div>
      <div className="relative">
        <div className="flex h-10 gap-0.5 overflow-hidden rounded-lg" aria-hidden="true">
          {ANATOMY.map((part, index) => (
            <div key={part.label} style={{ flexGrow: part.to - part.from }}
              className={`flex items-center justify-center text-[11px] font-semibold ${index === 0 || index === 3 ? "bg-foreground text-background" : "bg-muted text-foreground"}`}>
              <span className="truncate px-1">{part.label}</span>
            </div>
          ))}
        </div>
        <div className="mt-1 flex h-5 overflow-hidden rounded" aria-hidden="true">
          <div style={{ width: `${(4 / ANATOMY_END) * 100}%` }} className="rounded bg-[repeating-linear-gradient(135deg,var(--muted-foreground)_0_2px,transparent_2px_6px)] opacity-60" />
          <span className="ml-2 text-[11px] leading-5 text-muted-foreground">Hook title on screen for the first ~4 s</span>
        </div>
        <div className="mt-1 flex justify-between font-mono text-[11px] text-muted-foreground" aria-hidden="true"><span>0 s</span><span>{ANATOMY_END} s</span></div>
      </div>
      <div role="list" className="grid gap-x-6 gap-y-2 text-sm leading-6 sm:grid-cols-2">
        {ANATOMY.map((part) => (
          <div role="listitem" key={part.label}><span className="font-semibold text-foreground">{part.label}</span> <span className="font-mono text-xs text-muted-foreground">{part.from}–{part.to} s</span> · <span className="text-muted-foreground">{part.note}</span></div>
        ))}
      </div>
    </figure>
  );
}

const RULES = [
  { status: "ok", Icon: CircleCheck, label: "Usually fine", items: ["Your own gameplay with your commentary", "Story moments inside a narrated playthrough", "Streams whose owner runs a clipping campaign, within its rules"] },
  { status: "warn", Icon: TriangleAlert, label: "Check first", items: ["Monetizing: Rockstar’s policy is written for non-commercial use", "Licensed radio songs: can be muted or claimed", "Someone else’s stream with no campaign or permission"] },
  { status: "no", Icon: CircleX, label: "Gets removed", items: ["The ending or a big story reveal", "Isolated cutscenes and cinematic compilations", "Leaks, cheats and unauthorized mods"] },
] as const;

const RULE_TONE = {
  ok: "text-green-700 dark:text-green-400",
  warn: "text-amber-700 dark:text-amber-400",
  no: "text-red-700 dark:text-red-400",
} as const;

export function RulesBoard() {
  return (
    <figure className="not-prose space-y-3" aria-labelledby="rules-board-title">
      <figcaption id="rules-board-title" className="flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-muted-foreground"><ShieldAlert className="size-4" aria-hidden="true" />What you can post · a summary, not legal advice</figcaption>
      <div className="grid gap-3 md:grid-cols-3">
        {RULES.map(({ status, Icon, label, items }) => (
          <section key={status} className="rounded-xl border bg-background p-4" aria-label={label}>
            <p className={`flex items-center gap-2 font-semibold ${RULE_TONE[status]}`}><Icon className="size-4" aria-hidden="true" />{label}</p>
            <ul className="mt-3 space-y-2 text-sm leading-5 text-muted-foreground" style={{ marginLeft: "1rem" }}>{items.map((item) => <li key={item}>{item}</li>)}</ul>
          </section>
        ))}
      </div>
    </figure>
  );
}

const LAUNCH_WEEK = [
  ["Before Nov 19", "Set up", "Connect TikTok and YouTube, pick caption style, read the campaign rules you want to join. Pre-release footage gets removed, so don't post any."],
  ["Nov 19 (launch)", "Stream or watch", "Play or follow the streams you're allowed to clip. Write down timestamps of silent plays while they happen."],
  ["Nov 20", "First batch", "Import yesterday's VODs. Keep the 3–5 strongest spoken moments; finish them properly instead of posting twenty rough ones."],
  ["Nov 21–23", "Steady output", "One VOD in, a few clips out, every day. Avoid the story: spoiler clips get reported and removed."],
  ["Nov 24–25", "Read the numbers", "Compare which kinds of moment kept people watching: chases, reactions, map discoveries, takes. Clip more of those."],
] as const;

export function LaunchWeekTable() {
  return (
    <div className="not-prose overflow-x-auto rounded-xl border">
      <table className="w-full min-w-[560px] text-left text-sm leading-6">
        <caption className="border-b px-4 py-3 text-left font-medium text-foreground">A launch-week plan for GTA 6 clips (launch: November 19, 2026)</caption>
        <thead className="bg-muted/50"><tr><th scope="col" className="p-4">When</th><th scope="col" className="p-4">Focus</th><th scope="col" className="p-4">What to do</th></tr></thead>
        <tbody>{LAUNCH_WEEK.map(([when, focus, what]) => (
          <tr key={when} className="border-t"><th scope="row" className="whitespace-nowrap p-4 align-top font-mono text-xs">{when}</th><td className="p-4 align-top font-medium text-foreground">{focus}</td><td className="p-4 align-top text-muted-foreground">{what}</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}
