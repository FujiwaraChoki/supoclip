"use client";

import { useState } from "react";
import { Ban, Eye, MessageSquareText } from "lucide-react";

import { getGta6Copy, type Gta6MomentKind, type GuideLocale } from "@/lib/localized-guides";

const STREAM_MINUTES = 240;

const KIND_STYLES: Record<Gta6MomentKind, { Icon: typeof Eye; dot: string }> = {
  spoken: { Icon: MessageSquareText, dot: "bg-[var(--moment-spoken)]" },
  silent: { Icon: Eye, dot: "bg-[var(--moment-silent)]" },
  skip: { Icon: Ban, dot: "bg-transparent border-2 border-dashed border-muted-foreground" },
};

const KIND_KEYS: Gta6MomentKind[] = ["spoken", "silent", "skip"];

function clock(minute: number) {
  return `${Math.floor(minute / 60)}:${String(minute % 60).padStart(2, "0")}`;
}

export function MomentMap({ locale = "en" }: { locale?: GuideLocale }) {
  const [active, setActive] = useState(4);
  const copy = getGta6Copy(locale).momentMap;
  const moment = copy.moments[active] ?? copy.moments[0];
  const kindCopy = copy.kinds[moment.kind];
  const KindIcon = KIND_STYLES[moment.kind].Icon;

  return (
    <figure className="moment-map not-prose space-y-5 rounded-2xl border bg-muted/20 p-5 sm:p-7" aria-labelledby="moment-map-title">
      <style>{`
        .moment-map { --moment-spoken: #2a78d6; --moment-silent: #eb6834; }
        .dark .moment-map { --moment-spoken: #3987e5; --moment-silent: #d95926; }
      `}</style>
      <div className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">{copy.eyebrow}</p>
        <h3 id="moment-map-title" className="text-xl font-bold tracking-tight text-foreground">{copy.title}</h3>
        <p className="text-sm leading-6 text-muted-foreground">{copy.description}</p>
      </div>

      <ul className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground" style={{ listStyle: "none", marginLeft: 0 }} aria-label={copy.legendAria}>
        {KIND_KEYS.map((key) => {
          const { Icon, dot } = KIND_STYLES[key];
          return <li key={key} className="inline-flex items-center gap-2"><span className={`size-3 rounded-full ${dot}`} aria-hidden="true" /><Icon className="size-3.5" aria-hidden="true" />{copy.kinds[key].short}</li>;
        })}
      </ul>

      <div className="relative px-2 pt-2 pb-8">
        <div className="h-2 rounded-full bg-muted" aria-hidden="true" />
        <div className="relative -mt-[14px] h-6" role="group" aria-label={copy.momentsAria}>
          {copy.moments.map((item, index) => {
            const selected = index === active;
            return (
              <button
                key={item.minute}
                type="button"
                onClick={() => setActive(index)}
                onMouseEnter={() => setActive(index)}
                onFocus={() => setActive(index)}
                aria-pressed={selected}
                aria-label={`${clock(item.minute)} ${item.title}. ${copy.kinds[item.kind].short}`}
                className="group absolute top-0 flex size-8 -translate-x-1/2 items-center justify-center rounded-full outline-none focus-visible:ring-2 focus-visible:ring-ring"
                style={{ left: `${(item.minute / STREAM_MINUTES) * 100}%`, marginTop: -4 }}
              >
                <span className={`block rounded-full ring-2 ring-background transition-all ${KIND_STYLES[item.kind].dot} ${selected ? "size-5" : "size-3.5 group-hover:size-4.5"}`} />
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
          <KindIcon className="size-3.5" aria-hidden="true" />{kindCopy.label}
        </p>
        <div className="mt-2 font-semibold text-foreground"><span className="font-mono text-muted-foreground">{clock(moment.minute)}</span> · {moment.title}</div>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">{moment.note}</p>
      </div>

      <details className="text-sm text-muted-foreground">
        <summary className="cursor-pointer font-medium text-foreground">{copy.showTableSummary}</summary>
        <table className="mt-3 w-full text-left text-xs leading-5">
          <thead><tr className="border-b"><th scope="col" className="py-2 pr-3">{copy.tableHeaders[0]}</th><th scope="col" className="py-2 pr-3">{copy.tableHeaders[1]}</th><th scope="col" className="py-2">{copy.tableHeaders[2]}</th></tr></thead>
          <tbody>{copy.moments.map((item) => (
            <tr key={item.minute} className="border-b last:border-0"><td className="py-2 pr-3 font-mono">{clock(item.minute)}</td><td className="py-2 pr-3">{item.title}</td><td className="py-2">{copy.kinds[item.kind].short}</td></tr>
          ))}</tbody>
        </table>
      </details>
    </figure>
  );
}
