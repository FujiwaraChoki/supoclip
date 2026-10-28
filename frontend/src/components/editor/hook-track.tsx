"use client";
import { useRef } from "react";
import { useTranslations } from "next-intl";
import { editDuration, hookRange, timecode, trimHook, uid, type EditDocument } from "@/lib/editor/document";
import { prepareTextDraft, type TextDraft } from "@/lib/editor/render";
import { InlineText } from "./inline-text";

/** Hook timing is in output seconds, so its edges stay meaningful across cuts. */
export function HookTrack({ doc, pause, seek, update, onDraft, secondsPerPixel }: {
  doc: EditDocument;
  pause: () => void;
  seek: (time: number) => void;
  update: (change: (doc: EditDocument) => EditDocument, group?: string) => void;
  onDraft: (draft: TextDraft | undefined) => void;
  secondsPerPixel: () => number;
}) {
  const t = useTranslations("editor");
  const range = hookRange(doc), total = editDuration(doc);
  const drag = useRef<{ document: EditDocument; edge: "start" | "end"; x: number; scale: number; group: string } | null>(null);
  return <div role="group" aria-label={t("captions.hookTitle")} className="relative h-7">
    <div className="absolute inset-y-0 min-w-2 rounded-md bg-amber-100 dark:bg-amber-950"
      style={{ left: `${range.start / total * 100}%`, width: `${(range.end - range.start) / total * 100}%` }}>
      <InlineText value={doc.hookTitle ?? ""} label={t("captions.hookTitle")} maxLength={200}
        buttonLabel={`${t("captions.hookTitle")}: ${doc.hookTitle}`}
        title={`${doc.hookTitle} · ${timecode(range.start)} – ${timecode(range.end)}`}
        className="absolute inset-0 h-full w-full overflow-hidden rounded-md bg-transparent px-3 text-left text-[11px] leading-7 text-amber-950 hover:bg-amber-200 dark:text-amber-100"
        onBegin={() => { pause(); seek(range.start + Math.min(.3, (range.end - range.start) / 2)); }}
        onDraft={text => onDraft(text === null ? undefined : prepareTextDraft(doc, range.start, "hook", text))}
        onCommit={hookTitle => update(current => ({ ...current, hookTitle }))}>
        <span className="block truncate">{doc.hookTitle}</span>
      </InlineText>
      {(["start", "end"] as const).map(edge => <div key={edge} role="slider" tabIndex={0}
        aria-label={t(edge === "start" ? "timeline.hookStart" : "timeline.hookEnd")}
        aria-orientation="horizontal" aria-valuemin={0} aria-valuemax={total} aria-valuenow={range[edge]}
        aria-valuetext={timecode(range[edge])}
        className={`absolute inset-y-0 z-10 flex w-2.5 touch-none cursor-ew-resize items-center justify-center rounded-sm outline-none hover:bg-amber-400/50 focus-visible:ring-2 focus-visible:ring-amber-700 ${edge === "start" ? "left-0" : "right-0"}`}
        onPointerDown={event => {
          event.preventDefault(); event.stopPropagation(); pause();
          drag.current = { document: doc, edge, x: event.clientX, scale: secondsPerPixel(), group: `gesture:${uid()}` };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={event => {
          const from = drag.current;
          if (!from || !(event.buttons & 1)) return;
          const value = hookRange(from.document)[from.edge] + (event.clientX - from.x) * from.scale;
          update(current => ({ ...current, hookTiming: trimHook(from.document, from.edge, value).hookTiming }), from.group);
        }}
        onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}
        onKeyDown={event => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault(); event.stopPropagation(); pause();
          const value = event.key === "Home" ? 0 : event.key === "End" ? total : range[edge] + (event.key === "ArrowLeft" ? -1 : 1) * (event.shiftKey ? 1 : .1);
          update(current => trimHook(current, edge, value));
        }}>
        <span className="pointer-events-none h-3 w-0.5 rounded bg-amber-800/60 dark:bg-amber-100/70" />
      </div>)}
    </div>
  </div>;
}
