"use client";

import { useState } from "react";
import { Ban, Eye, MessageSquareText } from "lucide-react";

type Kind = "spoken" | "silent" | "skip";

interface Moment {
  minute: number;
  kind: Kind;
  title: string;
  note: string;
}

const STREAM_MINUTES = 240;

const KINDS: Record<Kind, { label: string; short: string; Icon: typeof Eye; dot: string }> = {
  spoken: { label: "Spoken moment: the AI shortlist can find it", short: "AI finds it", Icon: MessageSquareText, dot: "bg-[var(--moment-spoken)]" },
  silent: { label: "Silent action: check it yourself", short: "Review by hand", Icon: Eye, dot: "bg-[var(--moment-silent)]" },
  skip: { label: "Story spoiler or cutscene: leave it out", short: "Skip", Icon: Ban, dot: "bg-transparent border-2 border-dashed border-muted-foreground" },
};

/** A made-up but typical four-hour launch stream, used to show which moments need which review. */
const MOMENTS: Moment[] = [
  { minute: 6, kind: "spoken", title: "First reaction to the opening view", note: "The streamer says what they see and why it surprises them. Speech carries it, so a transcript can find it." },
  { minute: 31, kind: "skip", title: "Story cutscene", note: "Isolated cutscenes are among the things Rockstar's video policy says it removes. Keep story beats inside commentary, or leave them out." },
  { minute: 58, kind: "silent", title: "Near-miss on the highway", note: "A great play with no words over it. A speech-led shortlist can miss it; note the timestamp while you watch." },
  { minute: 84, kind: "spoken", title: "Chat asks about the map size", note: "A question plus a clear answer. Keep the question on screen or in the first sentence." },
  { minute: 112, kind: "spoken", title: "Five-star chase, narrated", note: "Action with running commentary. Keep the setup, the chase and the reaction in one cut." },
  { minute: 139, kind: "silent", title: "Physics glitch", note: "Funny to watch, nobody explains it. Find it by scrubbing, then frame the action, not the face." },
  { minute: 171, kind: "spoken", title: "Hot take on the soundtrack", note: "An opinion with a reason. Mute or swap licensed radio music before posting." },
  { minute: 203, kind: "skip", title: "Story ending reveal", note: "Posting the ending or a big reveal is exactly what the policy targets, and it gets reported." },
  { minute: 226, kind: "spoken", title: "Wrap-up: the best moment of the day", note: "The streamer summarises their own highlight. Useful for a hook title and as a pointer to the real moment." },
];

function clock(minute: number) {
  return `${Math.floor(minute / 60)}:${String(minute % 60).padStart(2, "0")}`;
}

export function MomentMap() {
  const [active, setActive] = useState(4);
  const moment = MOMENTS[active];
  const kind = KINDS[moment.kind];

  return (
    <figure className="moment-map not-prose space-y-5 rounded-2xl border bg-muted/20 p-5 sm:p-7" aria-labelledby="moment-map-title">
      <style>{`
        .moment-map { --moment-spoken: #2a78d6; --moment-silent: #eb6834; }
        .dark .moment-map { --moment-spoken: #3987e5; --moment-silent: #d95926; }
      `}</style>
      <div className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Moment map · example stream</p>
        <h3 id="moment-map-title" className="text-xl font-bold tracking-tight text-foreground">One four-hour GTA 6 stream, three kinds of moment</h3>
        <p className="text-sm leading-6 text-muted-foreground">Select a marker. SupoClip reads the transcript, so spoken moments land on its shortlist; silent plays need your eyes, and spoilers stay out.</p>
      </div>

      <ul className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground" style={{ listStyle: "none", marginLeft: 0 }} aria-label="Legend">
        {(Object.keys(KINDS) as Kind[]).map((key) => {
          const { short, Icon, dot } = KINDS[key];
          return <li key={key} className="inline-flex items-center gap-2"><span className={`size-3 rounded-full ${dot}`} aria-hidden="true" /><Icon className="size-3.5" aria-hidden="true" />{short}</li>;
        })}
      </ul>

      <div className="relative px-2 pt-2 pb-8">
        <div className="h-2 rounded-full bg-muted" aria-hidden="true" />
        <div className="relative -mt-[14px] h-6" role="group" aria-label="Moments in the stream">
          {MOMENTS.map((item, index) => {
            const selected = index === active;
            return (
              <button
                key={item.minute}
                type="button"
                onClick={() => setActive(index)}
                onMouseEnter={() => setActive(index)}
                onFocus={() => setActive(index)}
                aria-pressed={selected}
                aria-label={`${clock(item.minute)} ${item.title}. ${KINDS[item.kind].short}`}
                className="group absolute top-0 flex size-8 -translate-x-1/2 items-center justify-center rounded-full outline-none focus-visible:ring-2 focus-visible:ring-ring"
                style={{ left: `${(item.minute / STREAM_MINUTES) * 100}%`, marginTop: -4 }}
              >
                <span className={`block rounded-full ring-2 ring-background transition-all ${KINDS[item.kind].dot} ${selected ? "size-5" : "size-3.5 group-hover:size-4.5"}`} />
              </button>
            );
          })}
        </div>
        <div className="absolute inset-x-2 bottom-0 flex justify-between font-mono text-[11px] text-muted-foreground" aria-hidden="true">
          {[0, 60, 120, 180, 240].map((minute) => <span key={minute}>{clock(minute)}</span>)}
        </div>
      </div>

      <div className="rounded-xl border bg-background p-4" aria-live="polite">
        <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          <kind.Icon className="size-3.5" aria-hidden="true" />{kind.label}
        </p>
        <div className="mt-2 font-semibold text-foreground"><span className="font-mono text-muted-foreground">{clock(moment.minute)}</span> · {moment.title}</div>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">{moment.note}</p>
      </div>

      <details className="text-sm text-muted-foreground">
        <summary className="cursor-pointer font-medium text-foreground">Show every moment as a table</summary>
        <table className="mt-3 w-full text-left text-xs leading-5">
          <thead><tr className="border-b"><th scope="col" className="py-2 pr-3">Time</th><th scope="col" className="py-2 pr-3">Moment</th><th scope="col" className="py-2">Review</th></tr></thead>
          <tbody>{MOMENTS.map((item) => (
            <tr key={item.minute} className="border-b last:border-0"><td className="py-2 pr-3 font-mono">{clock(item.minute)}</td><td className="py-2 pr-3">{item.title}</td><td className="py-2">{KINDS[item.kind].short}</td></tr>
          ))}</tbody>
        </table>
      </details>
    </figure>
  );
}
