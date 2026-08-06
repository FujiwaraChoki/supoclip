"use client";

import {
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Captions,
  Copy,
  Film,
  Image as ImageIcon,
  Lock,
  Music2,
  Scissors,
  Trash2,
  Type,
  ZoomIn,
  ZoomOut,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { formatTime } from "./editor-utils";
import type { EditorProject, TimelineItem, TimelineTrack } from "./types";

interface EditorTimelineProps {
  project: EditorProject;
  currentTime: number;
  selectedItemId: string | null;
  onSeek: (time: number) => void;
  onSelectItem: (itemId: string | null) => void;
  onUpdateItem: (itemId: string, updates: Partial<TimelineItem>) => void;
  onBeginTransform: () => void;
  onEndTransform: () => void;
  onSplit: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}

const TRACKS: Array<{
  id: TimelineTrack;
  name: string;
  icon: typeof Film;
}> = [
  { id: "text", name: "Text", icon: Type },
  { id: "overlay", name: "Overlay", icon: ImageIcon },
  { id: "main", name: "Main video", icon: Film },
  { id: "audio", name: "Audio", icon: Music2 },
];

const TRACK_HEIGHT = 58;
const LABEL_WIDTH = 124;
const BASE_PIXELS_PER_SECOND = 44;

function itemPalette(item: TimelineItem) {
  if (item.type === "audio") return "border-white/15 bg-white/[0.07] text-zinc-200";
  if (item.track === "main") return "border-white/20 bg-white/15 text-zinc-100";
  return "border-white/15 bg-white/10 text-zinc-200";
}

function ItemIcon({ item }: { item: TimelineItem }) {
  if (item.type === "audio") return <Music2 className="size-3" />;
  if (item.type === "text") return <Type className="size-3" />;
  if (item.type === "caption") return <Captions className="size-3" />;
  if (item.type === "image") return <ImageIcon className="size-3" />;
  return <Film className="size-3" />;
}

function trackPointerGesture(
  onMove: (event: PointerEvent) => void,
  onFinish: () => void,
) {
  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", finish);
    window.removeEventListener("pointercancel", finish);
    window.removeEventListener("blur", finish);
    onFinish();
  };
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", finish, { once: true });
  window.addEventListener("pointercancel", finish, { once: true });
  window.addEventListener("blur", finish, { once: true });
}

export function EditorTimeline({
  project,
  currentTime,
  selectedItemId,
  onSeek,
  onSelectItem,
  onUpdateItem,
  onBeginTransform,
  onEndTransform,
  onSplit,
  onDuplicate,
  onDelete,
}: EditorTimelineProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [zoom, setZoom] = useState(1);
  const pixelsPerSecond = BASE_PIXELS_PER_SECOND * zoom;
  const duration = Math.max(project.duration, 10);
  const timelineWidth = Math.max(duration * pixelsPerSecond + 96, 720);
  const selectedItem = project.items.find((item) => item.id === selectedItemId) ?? null;

  const tickStep = useMemo(() => {
    if (pixelsPerSecond >= 110) return 0.5;
    if (pixelsPerSecond >= 55) return 1;
    if (pixelsPerSecond >= 28) return 2;
    return 5;
  }, [pixelsPerSecond]);

  const ticks = useMemo(() => {
    const result: number[] = [];
    for (let time = 0; time <= duration + tickStep; time += tickStep) result.push(time);
    return result;
  }, [duration, tickStep]);

  useEffect(() => {
    const scroller = scrollRef.current;
    if (!scroller) return;
    const x = currentTime * pixelsPerSecond;
    if (x < scroller.scrollLeft || x > scroller.scrollLeft + scroller.clientWidth - 100) {
      scroller.scrollTo({ left: Math.max(0, x - scroller.clientWidth / 2), behavior: "smooth" });
    }
  }, [currentTime, pixelsPerSecond]);

  const seekFromEvent = (event: ReactPointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    onSeek(Math.min(project.duration, Math.max(0, x / pixelsPerSecond)));
  };

  const beginItemMove = (event: ReactPointerEvent, item: TimelineItem) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    onSelectItem(item.id);
    if (item.locked) return;
    onBeginTransform();
    const initialClientX = event.clientX;
    const initialStart = item.start;

    const onMove = (moveEvent: PointerEvent) => {
      const delta = (moveEvent.clientX - initialClientX) / pixelsPerSecond;
      const rawStart = Math.max(0, initialStart + delta);
      const snapped = Math.round(rawStart * 10) / 10;
      onUpdateItem(item.id, { start: Math.min(Math.max(0, project.duration - item.duration), snapped) });
    };
    trackPointerGesture(onMove, onEndTransform);
  };

  const beginTrim = (event: ReactPointerEvent, item: TimelineItem, edge: "left" | "right") => {
    if (item.locked || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    onSelectItem(item.id);
    onBeginTransform();
    const initialClientX = event.clientX;
    const initialStart = item.start;
    const initialDuration = item.duration;
    const initialTrim = item.trimStart;

    const onMove = (moveEvent: PointerEvent) => {
      const delta = Math.round(((moveEvent.clientX - initialClientX) / pixelsPerSecond) * 20) / 20;
      if (edge === "left") {
        const sourceLimited = item.type === "video" || item.type === "audio";
        const minimumDelta = sourceLimited
          ? Math.max(-initialStart, -initialTrim / Math.max(0.25, item.speed))
          : -initialStart;
        const boundedDelta = Math.max(minimumDelta, Math.min(initialDuration - 0.1, delta));
        onUpdateItem(item.id, {
          start: initialStart + boundedDelta,
          duration: initialDuration - boundedDelta,
          trimStart: Math.max(0, initialTrim + boundedDelta * item.speed),
        });
      } else {
        const maxDuration = Math.max(0.1, project.duration - item.start);
        onUpdateItem(item.id, { duration: Math.max(0.1, Math.min(maxDuration, initialDuration + delta)) });
      }
    };
    trackPointerGesture(onMove, onEndTransform);
  };

  return (
    <section className="flex h-[280px] min-h-[220px] flex-col border-t border-white/10 bg-[#121212] text-white">
      <div className="flex h-11 shrink-0 items-center gap-1 border-b border-white/10 px-3">
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          title="Split at playhead (S)"
          className="text-zinc-300 hover:bg-white/10 hover:text-white"
          disabled={!selectedItem || currentTime <= selectedItem.start || currentTime >= selectedItem.start + selectedItem.duration}
          onClick={onSplit}
        >
          <Scissors />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          title="Duplicate (⌘D)"
          className="text-zinc-300 hover:bg-white/10 hover:text-white"
          disabled={!selectedItem}
          onClick={onDuplicate}
        >
          <Copy />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          title="Delete"
          className="text-zinc-300 hover:bg-red-500/15 hover:text-red-300"
          disabled={!selectedItem}
          onClick={onDelete}
        >
          <Trash2 />
        </Button>
        <div className="mx-2 h-5 w-px bg-white/10" />
        <div className="font-mono text-xs text-zinc-400">
          <span className="text-white">{formatTime(currentTime, true)}</span>
          <span className="mx-1.5">/</span>
          {formatTime(project.duration, true)}
        </div>
        <div className="ml-auto flex items-center gap-2 text-zinc-400">
          <ZoomOut className="size-3.5" />
          <input
            type="range"
            min={0.4}
            max={3}
            step={0.1}
            value={zoom}
            aria-label="Timeline zoom"
            className="h-1 w-28 accent-white"
            onChange={(event) => setZoom(Number(event.target.value))}
          />
          <ZoomIn className="size-3.5" />
          <span className="w-9 text-right text-[11px]">{Math.round(zoom * 100)}%</span>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[124px_minmax(0,1fr)]">
        <div className="z-30 border-r border-white/10 bg-[#141414]">
          <div className="h-7 border-b border-white/10 px-3 text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-500 flex items-center">
            Tracks
          </div>
          {TRACKS.map((track) => {
            const Icon = track.icon;
            return (
              <div
                key={track.id}
                className="flex items-center gap-2 border-b border-white/[0.07] px-3 text-xs font-medium text-zinc-300"
                style={{ height: TRACK_HEIGHT }}
              >
                <Icon className="size-3.5 text-zinc-500" />
                <span className="truncate">{track.name}</span>
                {project.items.some((item) => item.track === track.id && item.locked) ? (
                  <Lock className="ml-auto size-3 text-zinc-600" />
                ) : null}
              </div>
            );
          })}
        </div>

        <div ref={scrollRef} className="min-w-0 overflow-auto bg-[#0f0f0f]">
          <div className="relative" style={{ width: timelineWidth, minHeight: TRACKS.length * TRACK_HEIGHT + 28 }}>
            <div
              className="sticky top-0 z-20 h-7 cursor-crosshair border-b border-white/10 bg-[#141414]"
              onPointerDown={seekFromEvent}
            >
              {ticks.map((time) => {
                const major = Math.abs(time - Math.round(time)) < 0.001;
                return (
                  <div
                    key={time}
                    className="absolute bottom-0 border-l border-white/15"
                    style={{ left: time * pixelsPerSecond, height: major ? 13 : 7 }}
                  >
                    {major ? (
                      <span className="absolute bottom-3 left-1 font-mono text-[9px] text-zinc-500">
                        {formatTime(time)}
                      </span>
                    ) : null}
                  </div>
                );
              })}
            </div>

            {TRACKS.map((track, trackIndex) => (
              <div
                key={track.id}
                className="relative border-b border-white/[0.07] bg-[linear-gradient(90deg,rgba(255,255,255,.025)_1px,transparent_1px)]"
                style={{
                  height: TRACK_HEIGHT,
                  backgroundSize: `${pixelsPerSecond}px 100%`,
                }}
                onPointerDown={(event) => {
                  const rect = event.currentTarget.getBoundingClientRect();
                  onSeek(Math.min(project.duration, Math.max(0, (event.clientX - rect.left) / pixelsPerSecond)));
                  onSelectItem(null);
                }}
              >
                {project.items
                  .filter((item) => item.track === track.id)
                  .map((item) => {
                    const selected = item.id === selectedItemId;
                    return (
                      <div
                        key={item.id}
                        className={cn(
                          "group absolute top-1.5 h-[45px] touch-none overflow-hidden rounded-md border shadow-sm",
                          itemPalette(item),
                          item.locked ? "cursor-not-allowed" : "cursor-grab active:cursor-grabbing",
                          selected && "ring-2 ring-white ring-offset-1 ring-offset-[#0f0f0f]",
                        )}
                        style={{
                          left: item.start * pixelsPerSecond,
                          width: Math.max(12, item.duration * pixelsPerSecond),
                          zIndex: selected ? 10 : trackIndex + 1,
                        }}
                        onPointerDown={(event) => beginItemMove(event, item)}
                      >
                        <button
                          type="button"
                          aria-label={`Trim start of ${item.name}`}
                          className="absolute inset-y-0 left-0 z-10 w-2 cursor-ew-resize border-r border-white/30 bg-white/0 hover:bg-white/30"
                          onPointerDown={(event) => beginTrim(event, item, "left")}
                        />
                        <div className="flex h-full min-w-0 items-center gap-1.5 px-3">
                          <ItemIcon item={item} />
                          <span className="truncate text-[11px] font-semibold">{item.name}</span>
                          <span className="ml-auto hidden shrink-0 font-mono text-[9px] opacity-65 group-hover:block">
                            {item.duration.toFixed(1)}s
                          </span>
                        </div>
                        <button
                          type="button"
                          aria-label={`Trim end of ${item.name}`}
                          className="absolute inset-y-0 right-0 z-10 w-2 cursor-ew-resize border-l border-white/30 bg-white/0 hover:bg-white/30"
                          onPointerDown={(event) => beginTrim(event, item, "right")}
                        />
                      </div>
                    );
                  })}
              </div>
            ))}

            <div
              className="pointer-events-none absolute top-0 z-50 w-px bg-white"
              style={{ left: currentTime * pixelsPerSecond, height: TRACKS.length * TRACK_HEIGHT + 28 }}
            >
              <div className="absolute -left-[5px] top-0 h-0 w-0 border-x-[5px] border-t-[7px] border-x-transparent border-t-white" />
            </div>
          </div>
        </div>
      </div>
      <div className="sr-only" style={{ width: LABEL_WIDTH }} />
    </section>
  );
}
