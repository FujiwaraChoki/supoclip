"use client";
import {
  ArrowLeft,
  ArrowRight,
  AudioLines,
  Captions,
  Clapperboard,
  CaptionsOff,
  Copy,
  Film,
  Scissors,
  Trash2,
  Type,
  Volume2,
  VolumeX,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import {
  captionGroups,
  clamp,
  editDuration,
  hookRange,
  locate,
  mappedBroll,
  mappedWords,
  outputTime,
  placeBrollOnTimeline,
  replaceCaptionGroup,
  sequence,
  splitSegment,
  timecode,
  uid,
  type EditDocument,
  type EditorState,
} from "@/lib/editor/document";
import { Panel, ToolButton } from "./studio-ui";
import { Filmstrip } from "./filmstrip";
import { InlineText } from "./inline-text";
import { HookTrack } from "./hook-track";
import { prepareTextDraft, type TextDraft } from "@/lib/editor/render";

interface Props {
  doc: EditDocument;
  asset: EditorState;
  endpoint: string;
  /** Playhead on the source timeline (the video element's time). */
  currentTime: number;
  onTextDraft: (draft: TextDraft | undefined) => void;
  index: number;
  select: (index: number) => void;
  pause: () => void;
  seek: (time: number) => void;
  update: (change: (doc: EditDocument) => EditDocument, group?: string) => void;
  brollId: string | null;
  selectBroll: (id: string | null) => void;
  /** Open the B-roll panel to add footage. */
  addBroll?: (time: number) => void;
  /** Seek for B-roll, which may sit in another segment. */
  seekBroll?: (time: number) => void;
}

const MIN_SEGMENT = 0.04;
const TRACK = {
  video: "h-12 [@media(max-height:780px)]:h-10",
  broll: "h-8",
  captions: "h-7",
  hook: "h-7",
  audio: "h-8",
};

/** Ruler label: whole seconds as m:ss, half seconds with one decimal. */
function rulerLabel(seconds: number, step: number) {
  const minutes = Math.floor(seconds / 60),
    rest = seconds - minutes * 60;
  return `${minutes}:${step < 1 ? rest.toFixed(1).padStart(4, "0") : String(Math.round(rest)).padStart(2, "0")}`;
}

/** A readable ruler step for the visible duration. */
function rulerStep(visibleSeconds: number) {
  const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120];
  return steps.find((step) => visibleSeconds / step <= 10) ?? 300;
}

/**
 * Classic sequence timeline: the edit as clips laid end to end on V1, with
 * B-roll, caption and audio tracks underneath and one playhead through all of
 * them. Times here are edit (output) seconds; the document stores source seconds.
 */
export function Timeline({
  doc,
  asset,
  endpoint,
  currentTime,
  onTextDraft,
  index,
  select,
  pause,
  seek,
  update,
  brollId,
  selectBroll,
  addBroll,
}: Props) {
  const t = useTranslations("editor");
  const [zoom, setZoom] = useState(1);
  const scroller = useRef<HTMLDivElement>(null);
  const lanes = useRef<HTMLDivElement>(null);
  const items = sequence(doc);
  const total = Math.max(0.01, editDuration(doc));
  const hook = doc.hookTitle?.trim() ? hookRange(doc) : null;
  const active = doc.segments[index] ?? doc.segments[0];
  const playhead = clamp(
    outputTime(doc, Math.min(index, doc.segments.length - 1), clamp(currentTime, active.start, active.end)),
    0,
    total,
  );
  const pct = (seconds: number) => `${(seconds / total) * 100}%`;
  const at = (clientX: number) => {
    const box = lanes.current?.getBoundingClientRect();
    return box ? clamp((clientX - box.left) / box.width, 0, 1) * total : 0;
  };
  const seekOut = (time: number) => {
    const target = locate(doc, time);
    select(target.index);
    seek(target.source);
  };
  const secondsPerPixel = () => total / (lanes.current?.getBoundingClientRect().width || 1);

  // Keep the playhead in view while zoomed in.
  useEffect(() => {
    const box = scroller.current;
    if (!box || zoom === 1) return;
    const x = (playhead / total) * box.scrollWidth;
    if (x < box.scrollLeft + 40 || x > box.scrollLeft + box.clientWidth - 40)
      box.scrollLeft = Math.max(0, x - box.clientWidth / 3);
  }, [playhead, total, zoom]);

  const setRange = (start: number, end: number) =>
    update(
      (d) => ({
        ...d,
        segments: d.segments.map((s) =>
          s.id === active.id
            ? {
                ...s,
                start: clamp(start, 0, end - MIN_SEGMENT),
                end: clamp(end, start + MIN_SEGMENT, asset.duration),
              }
            : s,
        ),
      }),
      "trim",
    );
  const move = (direction: number) => {
    const target = index + direction;
    if (target < 0 || target >= doc.segments.length) return;
    update((d) => {
      const segments = [...d.segments];
      [segments[index], segments[target]] = [segments[target], segments[index]];
      return { ...d, segments };
    });
    select(target);
  };
  const canSplit = currentTime > active.start + MIN_SEGMENT && currentTime < active.end - MIN_SEGMENT;

  // Scrub: press on the ruler or empty track space and drag.
  const scrub = useRef(false);
  const startScrub = (e: React.PointerEvent) => {
    if (e.target !== e.currentTarget) return;
    scrub.current = true;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    seekOut(at(e.clientX));
  };
  const moveScrub = (e: React.PointerEvent) => {
    if (scrub.current) seekOut(at(e.clientX));
  };
  const endScrub = () => {
    scrub.current = false;
  };
  const scrubbing = { onPointerDown: startScrub, onPointerMove: moveScrub, onPointerUp: endScrub, onPointerCancel: endScrub };

  // Trim a clip by dragging its edge; the clips after it ripple.
  const trim = useRef<{ id: string; edge: "start" | "end"; x: number; start: number; end: number; scale: number } | null>(null);
  const beginTrim = (e: React.PointerEvent, segmentIndex: number, edge: "start" | "end") => {
    e.stopPropagation();
    const segment = doc.segments[segmentIndex];
    select(segmentIndex);
    trim.current = { id: segment.id, edge, x: e.clientX, start: segment.start, end: segment.end, scale: secondsPerPixel() };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const moveTrim = (e: React.PointerEvent) => {
    const d = trim.current;
    if (!d) return;
    const delta = (e.clientX - d.x) * d.scale;
    update(
      (current) => ({
        ...current,
        segments: current.segments.map((s) =>
          s.id !== d.id
            ? s
            : d.edge === "start"
              ? { ...s, start: clamp(d.start + delta, 0, d.end - MIN_SEGMENT) }
              : { ...s, end: clamp(d.end + delta, d.start + MIN_SEGMENT, asset.duration) },
        ),
      }),
      "trim",
    );
  };

  const groups = captionGroups(mappedWords(doc), doc.captions.wordsPerLine);
  const pieces = mappedBroll(doc);
  const step = rulerStep(total / zoom);
  // Labels too close to the end would be clipped by the card edge.
  const ticks = Array.from({ length: Math.floor(total / step) + 1 }, (_, i) => i * step).filter(
    (tick) => tick === 0 || total - tick > step * 0.35,
  );

  // Track headers are icons; the name is in the tooltip and accessible label.
  const header = (icon: React.ReactNode, label: string, height: string, control?: React.ReactNode) => (
    <div className={cn("flex items-center justify-center text-muted-foreground", height)}>
      {control ?? (
        <span role="img" aria-label={label} title={label}>
          {icon}
        </span>
      )}
    </div>
  );
  const minor = Array.from({ length: Math.ceil(total / (step / 4)) }, (_, i) => (i * step) / 4).filter(
    (time) => Math.abs(time / step - Math.round(time / step)) > 1e-6,
  );

  return (
    <Panel aria-label={t("timeline.region")} className="flex min-h-0 flex-col lg:h-full">
      <div className="flex shrink-0 flex-wrap items-center gap-x-1 gap-y-1 px-3 pt-2 pb-1">
        <ToolButton
          label={t("timeline.split")}
          shortcut="S"
          size="icon-sm"
          disabled={!canSplit}
          onClick={() => update((d) => splitSegment(d, index, currentTime))}
        >
          <Scissors />
        </ToolButton>
        <ToolButton
          label={t("timeline.remove")}
          size="icon-sm"
          className="hover:text-destructive"
          disabled={doc.segments.length === 1}
          onClick={() => {
            update((d) => ({ ...d, segments: d.segments.filter((s) => s.id !== active.id) }));
            select(Math.max(0, index - 1));
          }}
        >
          <Trash2 />
        </ToolButton>
        <ToolButton
          label={t("timeline.duplicate")}
          size="icon-sm"
          onClick={() =>
            update((d) => ({
              ...d,
              segments: [...d.segments.slice(0, index + 1), { ...active, id: uid() }, ...d.segments.slice(index + 1)],
            }))
          }
        >
          <Copy />
        </ToolButton>
        <ToolButton label={t("timeline.moveEarlier")} size="icon-sm" disabled={index === 0} onClick={() => move(-1)}>
          <ArrowLeft />
        </ToolButton>
        <ToolButton
          label={t("timeline.moveLater")}
          size="icon-sm"
          disabled={index >= doc.segments.length - 1}
          onClick={() => move(1)}
        >
          <ArrowRight />
        </ToolButton>
        <Separator orientation="vertical" className="mx-1.5 !h-4" />
        {(["in", "out"] as const).map((edge) => (
          <label key={edge} htmlFor={`studio-${edge}`} className="flex items-center gap-1 text-xs text-muted-foreground">
            {t(`timeline.${edge}`)}
            <Input
              id={`studio-${edge}`}
              className="h-7 w-[4.5rem] rounded-md border-0 bg-stone-100 px-2 font-mono text-xs shadow-none [appearance:textfield] tabular-nums focus-visible:ring-2 dark:bg-stone-900 [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
              aria-label={t(edge === "in" ? "timeline.inPoint" : "timeline.outPoint")}
              type="number"
              min={edge === "in" ? 0 : active.start + MIN_SEGMENT}
              max={edge === "in" ? active.end - MIN_SEGMENT : asset.duration}
              step="0.01"
              value={Number((edge === "in" ? active.start : active.end).toFixed(2))}
              onChange={(e) =>
                edge === "in"
                  ? setRange(Number(e.target.value), active.end)
                  : setRange(active.start, Number(e.target.value))
              }
            />
          </label>
        ))}
        <ToolButton
          label={t("timeline.setIn")}
          shortcut="I"
          size="sm"
          className="h-7 rounded-md px-2 text-xs text-muted-foreground hover:text-foreground"
          onClick={() => setRange(clamp(currentTime, 0, active.end - MIN_SEGMENT), active.end)}
        >
          {t("timeline.setIn")}
        </ToolButton>
        <ToolButton
          label={t("timeline.setOut")}
          shortcut="O"
          size="sm"
          className="h-7 rounded-md px-2 text-xs text-muted-foreground hover:text-foreground"
          onClick={() => setRange(active.start, clamp(currentTime, active.start + MIN_SEGMENT, asset.duration))}
        >
          {t("timeline.setOut")}
        </ToolButton>
        <div className="ml-auto flex items-center gap-1.5">
          <span className="mr-2 font-mono text-xs text-muted-foreground tabular-nums max-xl:hidden">
            {t(doc.segments.length === 1 ? "timeline.summaryOne" : "timeline.summaryOther", {
              count: doc.segments.length,
              duration: timecode(total),
            })}
          </span>
          <ToolButton label={t("timeline.zoomOut")} size="icon-sm" disabled={zoom <= 1} onClick={() => setZoom((z) => Math.max(1, z / 1.5))}>
            <ZoomOut />
          </ToolButton>
          <Slider
            aria-label={t("timeline.zoom")}
            className="w-20"
            min={1}
            max={12}
            step={0.25}
            value={[zoom]}
            onValueChange={([value]) => setZoom(value)}
          />
          <ToolButton label={t("timeline.zoomIn")} size="icon-sm" disabled={zoom >= 12} onClick={() => setZoom((z) => Math.min(12, z * 1.5))}>
            <ZoomIn />
          </ToolButton>
        </div>
      </div>
      <div className="flex min-h-0 flex-1 overflow-y-auto pr-3 pb-2">
        <div className="w-11 shrink-0 space-y-1">
          <div className="h-7" />
          {header(<Film className="size-4" />, t("timeline.tracks.video"), TRACK.video)}
          {header(<Clapperboard className="size-4" />, t("timeline.tracks.broll"), TRACK.broll)}
          {hook && header(<Type className="size-4" />, t("captions.hookTitle"), TRACK.hook)}
          {header(
            null,
            t("timeline.tracks.captions"),
            TRACK.captions,
            <button
              type="button"
              aria-pressed={!doc.captions.enabled}
              aria-label={t(doc.captions.enabled ? "timeline.tracks.hideCaptions" : "timeline.tracks.showCaptions")}
              title={t(doc.captions.enabled ? "timeline.tracks.hideCaptions" : "timeline.tracks.showCaptions")}
              className="rounded-md p-1 hover:bg-accent hover:text-foreground"
              onClick={() => update((d) => ({ ...d, captions: { ...d.captions, enabled: !d.captions.enabled } }))}
            >
              {doc.captions.enabled ? <Captions className="size-4" /> : <CaptionsOff className="size-4" />}
            </button>,
          )}
          {header(
            <AudioLines className="size-4" />,
            t("timeline.tracks.audio"),
            TRACK.audio,
            asset.hasAudio ? (
              <button
                type="button"
                aria-pressed={doc.muted}
                aria-label={t(doc.muted ? "timeline.tracks.unmute" : "timeline.tracks.mute")}
                title={t(doc.muted ? "timeline.tracks.unmute" : "timeline.tracks.mute")}
                className="rounded-md p-1 hover:bg-accent hover:text-foreground"
                onClick={() => update((d) => ({ ...d, muted: !d.muted }))}
              >
                {doc.muted ? <VolumeX className="size-4" /> : <Volume2 className="size-4" />}
              </button>
            ) : undefined,
          )}
        </div>
        <div ref={scroller} className="min-w-0 flex-1 overflow-x-auto overflow-y-hidden">
          <div ref={lanes} className="relative space-y-1" style={{ width: `${zoom * 100}%` }}>
            <div className="relative h-7 cursor-ew-resize select-none" {...scrubbing}>
              {ticks.map((tick) => (
                <span
                  key={tick}
                  className="pointer-events-none absolute top-1 -translate-x-1/2 font-mono text-[10px] text-muted-foreground tabular-nums first:translate-x-0"
                  style={{ left: pct(tick) }}
                >
                  {rulerLabel(tick, step)}
                </span>
              ))}
              {minor.map((time) => (
                <span
                  key={time}
                  aria-hidden
                  className="pointer-events-none absolute top-[11px] size-[3px] -translate-x-1/2 rounded-full bg-stone-300 dark:bg-stone-700"
                  style={{ left: pct(time) }}
                />
              ))}
            </div>
            <div className={cn("relative", TRACK.video)} {...scrubbing}>
              {items.map((item) => {
                const length = item.end - item.start;
                const selected = item.index === index;
                return (
                  <div
                    key={item.segment.id}
                    role="button"
                    tabIndex={0}
                    aria-pressed={selected}
                    aria-label={`${t("timeline.segment", { n: item.index + 1 })}, ${timecode(item.segment.start)} – ${timecode(item.segment.end)}`}
                    className={cn(
                      "absolute inset-y-0 overflow-hidden rounded-lg bg-stone-300 outline-none focus-visible:ring-2 focus-visible:ring-ring dark:bg-stone-700",
                      selected && "ring-2 ring-foreground",
                    )}
                    style={{
                      left: pct(item.start),
                      width: pct(length),
                    }}
                    onPointerDown={(e) => {
                      e.stopPropagation();
                      seekOut(at(e.clientX));
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        select(item.index);
                        seek(item.segment.start);
                      }
                    }}
                  >
                    <Filmstrip url={`${endpoint}/media/thumbnails.jpg`} start={item.segment.start} end={item.segment.end} duration={asset.duration} />
                    <span className="pointer-events-none absolute bottom-1 left-2.5 max-w-[calc(100%-1.25rem)] truncate rounded bg-black/55 px-1.5 py-px text-[10px] font-medium text-white backdrop-blur-sm">
                      {t("timeline.segment", { n: item.index + 1 })}
                    </span>
                    {(["start", "end"] as const).map((edge) => (
                      <span
                        key={edge}
                        aria-hidden
                        className={cn(
                          "group/handle absolute inset-y-0 z-10 flex w-2.5 cursor-ew-resize items-center justify-center opacity-0 transition-opacity hover:opacity-100",
                          edge === "start" ? "left-0 rounded-l-lg" : "right-0 rounded-r-lg",
                          "bg-foreground",
                          selected && "opacity-100",
                        )}
                        onPointerDown={(e) => beginTrim(e, item.index, edge)}
                        onPointerMove={moveTrim}
                        onPointerUp={() => (trim.current = null)}
                        onPointerCancel={() => (trim.current = null)}
                      >
                        <span className="h-4 w-0.5 rounded-full bg-background/80" />
                      </span>
                    ))}
                  </div>
                );
              })}
            </div>
            <BRollTrack
              doc={doc}
              total={total}
              pieces={pieces}
              selected={brollId}
              select={selectBroll}
              seekOut={seekOut}
              update={update}
              add={addBroll}
              at={at}
              secondsPerPixel={secondsPerPixel}
              duration={asset.duration}
            />
            {hook && <HookTrack doc={doc} pause={pause} seek={seekOut} update={update} onDraft={onTextDraft} secondsPerPixel={secondsPerPixel} />}
            <div className={cn("relative", TRACK.captions, !doc.captions.enabled && "opacity-40")} {...scrubbing}>
              {groups.map((group) => (
                <InlineText
                  key={`${group[0].id}-${group[0].start}`}
                  value={group.map(w => w.text).join(" ")}
                  label={t("timeline.tracks.captions")}
                  maxLength={960}
                  className="absolute inset-y-0 overflow-hidden rounded-md bg-stone-100 px-1.5 text-left text-[11px] leading-7 text-stone-600 transition-colors hover:bg-stone-200 dark:bg-stone-800 dark:text-stone-300"
                  style={{ left: pct(group[0].start), width: pct(Math.max(0.05, group[group.length - 1].end - group[0].start)) }}
                  title={group.map((w) => w.text).join(" ")}
                  onBegin={() => { pause(); seekOut(group[0].start + 0.001); }}
                  onDraft={text => onTextDraft(text === null ? undefined : prepareTextDraft(doc, group[0].start, "captions", text))}
                  onCommit={text => update(current => replaceCaptionGroup(current, group.map(word => word.id), text, asset.duration))}
                >
                  <span className="block truncate">{group.map((w) => w.text).join(" ")}</span>
                </InlineText>
              ))}
            </div>
            <div className={cn("relative", TRACK.audio, doc.muted && "opacity-40")} {...scrubbing}>
              {asset.hasAudio &&
                items.map((item) => {
                  const n = asset.waveform.length;
                  const from = Math.floor((item.segment.start / asset.duration) * n);
                  const to = Math.max(from + 1, Math.ceil((item.segment.end / asset.duration) * n));
                  const slice = asset.waveform.slice(from, to);
                  return (
                    <svg
                      key={item.segment.id}
                      aria-hidden
                      className="pointer-events-none absolute top-0 h-8 text-stone-300 dark:text-stone-600"
                      style={{ left: pct(item.start), width: pct(item.end - item.start) }}
                      viewBox={`0 0 ${slice.length} 40`}
                      preserveAspectRatio="none"
                    >
                      {slice.map((v, i) => (
                        <rect key={i} x={i + 0.15} y={20 - Math.max(1, v * 18)} width="0.7" height={Math.max(2, v * 36)} fill="currentColor" />
                      ))}
                    </svg>
                  );
                })}
            </div>
            <div aria-hidden className="pointer-events-none absolute inset-y-0 z-20 w-0.5 -translate-x-1/2 rounded-full bg-foreground" style={{ left: pct(playhead) }}>
              <div className="absolute top-1 left-1/2 size-3 -translate-x-1/2 rounded-full bg-foreground ring-2 ring-background" />
            </div>
          </div>
        </div>
      </div>
      <label className="sr-only" htmlFor="studio-playhead">
        {t("timeline.playhead")}
      </label>
      <input
        id="studio-playhead"
        className="sr-only focus:not-sr-only"
        type="range"
        min={0}
        max={total}
        step={1 / asset.fps}
        value={playhead}
        onChange={(e) => seekOut(Number(e.target.value))}
      />
    </Panel>

  );
}

function BRollTrack({
  doc,
  total,
  pieces,
  selected,
  select,
  seekOut,
  update,
  add,
  at,
  secondsPerPixel,
  duration,
}: {
  doc: EditDocument;
  total: number;
  pieces: ReturnType<typeof mappedBroll>;
  selected: string | null;
  select: (id: string | null) => void;
  seekOut: (time: number) => void;
  update: Props["update"];
  add?: (time: number) => void;
  at: (clientX: number) => number;
  secondsPerPixel: () => number;
  duration: number;
}) {
  const t = useTranslations("editor");
  const drag = useRef<{
    id: string;
    mode: "move" | "start" | "end";
    x: number;
    document: EditDocument;
    piece: ReturnType<typeof mappedBroll>[number];
    group: string;
    scale: number;
    moved: boolean;
  } | null>(null);
  const laneRef = useRef<HTMLDivElement>(null);
  const ignoreClick = useRef(false);
  const pct = (seconds: number) => `${(seconds / total) * 100}%`;
  const begin = (e: React.PointerEvent, piece: ReturnType<typeof mappedBroll>[number], mode: "move" | "start" | "end") => {
    const clip = piece.clip;
    e.stopPropagation();
    select(clip.id);
    ignoreClick.current = true;
    drag.current = { id: clip.id, mode, x: e.clientX, document: doc, piece, group: `gesture:${uid()}`, scale: secondsPerPixel(), moved: false };
    // Crossing a cut changes the piece's segment/key. Capture on the stable lane.
    laneRef.current?.setPointerCapture(e.pointerId);
  };
  const move = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    if (!(e.buttons & 1)) {
      drag.current = null;
      return;
    }
    if (Math.abs(e.clientX - d.x) > 2) d.moved = true;
    if (!d.moved) return;
    const delta = (e.clientX - d.x) * d.scale;
    const placed = placeBrollOnTimeline(d.document, d.id, d.piece, d.mode, delta, duration);
    update(current => ({ ...current, broll: placed.broll }), d.group);
  };
  const end = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    if (d && !d.moved) seekOut(at(e.clientX));
  };
  return (
    <div
      role="group"
      aria-label={t("broll.laneLabel")}
      ref={laneRef}
      onPointerDown={e => { if (e.target === e.currentTarget) ignoreClick.current = false; }}
      onPointerMove={move}
      onPointerUp={end}
      onPointerCancel={() => (drag.current = null)}
      onLostPointerCapture={() => (drag.current = null)}
      className={cn("relative cursor-copy rounded-lg", TRACK.broll, pieces.length === 0 && "bg-stone-50 dark:bg-stone-900/50")}
      onClick={(e) => {
        if (ignoreClick.current) { ignoreClick.current = false; return; }
        if (e.target !== e.currentTarget) return;
        const time = at(e.clientX);
        select(null);
        seekOut(time);
        add?.(time);
      }}
    >
      {pieces.map((piece) => {
        const clip = piece.clip;
        return (
          <div
            key={JSON.stringify([piece.segmentId, clip.id])}
            role="button"
            tabIndex={0}
            aria-pressed={clip.id === selected}
            aria-label={t("broll.clipLabel", {
              query: clip.query || t("broll.untitled"),
              start: timecode(clip.start),
              end: timecode(clip.end),
            })}
            className={cn(
              "absolute inset-y-0 cursor-grab touch-none overflow-hidden rounded-lg bg-sky-100 bg-cover bg-center outline-none active:cursor-grabbing focus-visible:ring-2 focus-visible:ring-ring dark:bg-sky-950",
              clip.id === selected && "ring-2 ring-sky-500",
            )}
            style={{
              left: pct(piece.start),
              width: pct(piece.end - piece.start),
              backgroundImage: clip.footage.thumbnail ? `url(${clip.footage.thumbnail})` : undefined,
            }}
            onPointerDown={(e) => begin(e, piece, "move")}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                select(clip.id);
                seekOut(piece.start + 0.001);
              }
            }}
          >
            <span className="pointer-events-none absolute inset-0 bg-gradient-to-r from-sky-950/60 to-sky-950/10" />
            <span className="pointer-events-none relative block truncate px-2 text-[11px] leading-8 font-medium text-white">
              {clip.query || t("broll.untitled")}
            </span>
            {(["start", "end"] as const).map((edge) => (
              <span
                key={edge}
                aria-hidden
                className={cn("absolute inset-y-0 w-2 cursor-ew-resize hover:bg-white/40", edge === "start" ? "left-0" : "right-0")}
                onPointerDown={(e) => begin(e, piece, edge)}
              />
            ))}
          </div>
        );
      })}
      {pieces.length === 0 && (
        <span className="pointer-events-none absolute inset-0 flex items-center px-2.5 text-[11px] text-muted-foreground">
          {t("broll.laneEmpty")}
        </span>
      )}
    </div>
  );
}
