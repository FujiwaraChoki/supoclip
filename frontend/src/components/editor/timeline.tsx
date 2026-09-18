"use client";
import {
  ArrowLeft,
  ArrowRight,
  Copy,
  Scissors,
  Trash2,
  ZoomIn,
} from "lucide-react";
import { Slider } from "@/components/ui/slider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";
import {
  clamp,
  editDuration,
  splitSegment,
  timecode,
  uid,
  type EditDocument,
  type EditorState,
} from "@/lib/editor/document";
import { useState } from "react";
import { Keys, Panel, PanelHeader, ToolButton } from "./studio-ui";

interface Props {
  doc: EditDocument;
  asset: EditorState;
  endpoint: string;
  currentTime: number;
  index: number;
  select: (index: number) => void;
  seek: (time: number) => void;
  update: (change: (doc: EditDocument) => EditDocument, group?: string) => void;
}
export function Timeline({
  doc,
  asset,
  endpoint,
  currentTime,
  index,
  select,
  seek,
  update,
}: Props) {
  const [zoom, setZoom] = useState(1);
  const active = doc.segments[index] ?? doc.segments[0];
  const setRange = (start: number, end: number) =>
    update(
      (d) => ({
        ...d,
        segments: d.segments.map((s) =>
          s.id === active.id
            ? {
                ...s,
                start: clamp(start, 0, end - 0.04),
                end: clamp(end, start + 0.04, asset.duration),
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
  const canSplit =
    currentTime > active.start + 0.04 && currentTime < active.end - 0.04;
  return (
    <Panel aria-label="Clip timeline">
      <PanelHeader
        title="Timeline"
        meta={`${doc.segments.length} ${doc.segments.length === 1 ? "segment" : "segments"} · ${timecode(editDuration(doc))}`}
      >
        <div className="flex items-center gap-1.5 text-muted-foreground">
          <ZoomIn className="size-4" aria-hidden />
          <ToggleGroup
            type="single"
            size="sm"
            variant="outline"
            aria-label="Timeline zoom"
            value={String(zoom)}
            onValueChange={(value) => value && setZoom(Number(value))}
          >
            {[1, 2, 4].map((n) => (
              <ToggleGroupItem
                key={n}
                value={String(n)}
                className="px-2 text-xs tabular-nums"
              >
                {n * 100}%
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => update((d) => splitSegment(d, index, currentTime))}
          disabled={!canSplit}
        >
          <Scissors />
          Split at playhead
        </Button>
      </PanelHeader>
      <div className="overflow-x-auto px-4 pt-3 pb-2">
        <div style={{ minWidth: `${zoom * 100}%` }}>
          <div className="flex justify-between pb-1.5 text-[10px] tabular-nums text-muted-foreground">
            {Array.from({ length: 7 }, (_, i) => (
              <span key={i}>{timecode((asset.duration * i) / 6)}</span>
            ))}
          </div>
          <div
            className="relative h-20 cursor-pointer overflow-hidden rounded-lg bg-stone-900"
            onPointerDown={(e) => {
              if ((e.target as HTMLElement).closest('[role="slider"]')) return;
              const box = e.currentTarget.getBoundingClientRect();
              seek(
                clamp((e.clientX - box.left) / box.width, 0, 1) *
                  asset.duration,
              );
            }}
          >
            <div
              aria-hidden
              className="absolute inset-x-0 top-0 h-10 opacity-80"
              style={{
                backgroundImage: `url(${endpoint}/media/thumbnails.jpg)`,
                backgroundSize: "100% 100%",
              }}
            />
            <svg
              aria-hidden
              className="absolute bottom-1 left-0 h-8 w-full"
              viewBox="0 0 160 40"
              preserveAspectRatio="none"
            >
              {asset.waveform.map((v, i) => (
                <rect
                  key={i}
                  x={i}
                  y={20 - Math.max(1, v * 19)}
                  width="0.65"
                  height={Math.max(2, v * 38)}
                  fill="currentColor"
                  className="text-stone-300"
                  opacity=".75"
                />
              ))}
            </svg>
            <div
              aria-hidden
              className="absolute inset-y-0 left-0 bg-black/70"
              style={{ width: `${(active.start / asset.duration) * 100}%` }}
            />
            <div
              aria-hidden
              className="absolute inset-y-0 right-0 bg-black/70"
              style={{ width: `${(1 - active.end / asset.duration) * 100}%` }}
            />
            <div
              aria-hidden
              className="absolute inset-y-0 rounded-md border-2 border-white shadow-[0_0_0_1px_rgb(0_0_0/0.4)]"
              style={{
                left: `${(active.start / asset.duration) * 100}%`,
                width: `${((active.end - active.start) / asset.duration) * 100}%`,
              }}
            />
            <div
              aria-hidden
              className="pointer-events-none absolute inset-y-0 w-0.5 -translate-x-1/2 bg-amber-400 shadow"
              style={{ left: `${(currentTime / asset.duration) * 100}%` }}
            >
              <div className="-ml-[3px] h-2 w-2 rounded-b-sm bg-amber-400" />
            </div>
          </div>
          <div className="mt-3">
            <Slider
              aria-label="Trim range"
              min={0}
              max={asset.duration}
              step={1 / asset.fps}
              minStepsBetweenThumbs={1}
              value={[active.start, active.end]}
              onValueChange={([start, end]) => setRange(start, end)}
            />
          </div>
          <label className="sr-only" htmlFor="studio-playhead">
            Playhead
          </label>
          <input
            id="studio-playhead"
            className="sr-only focus:not-sr-only focus:w-full"
            type="range"
            min={0}
            max={asset.duration}
            step={1 / asset.fps}
            value={currentTime}
            onChange={(e) => seek(Number(e.target.value))}
          />
        </div>
      </div>
      <div className="flex flex-wrap items-end gap-x-3 gap-y-2 px-4 pt-1 pb-3">
        <div className="space-y-1.5">
          <Label
            htmlFor="studio-in"
            className="text-xs font-normal text-muted-foreground"
          >
            In
          </Label>
          <Input
            id="studio-in"
            className="h-8 w-24 [appearance:textfield] tabular-nums [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
            aria-label="In point seconds"
            type="number"
            min={0}
            max={active.end - 0.04}
            step="0.01"
            value={Number(active.start.toFixed(2))}
            onChange={(e) => setRange(Number(e.target.value), active.end)}
          />
        </div>
        <div className="space-y-1.5">
          <Label
            htmlFor="studio-out"
            className="text-xs font-normal text-muted-foreground"
          >
            Out
          </Label>
          <Input
            id="studio-out"
            className="h-8 w-24 [appearance:textfield] tabular-nums [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
            aria-label="Out point seconds"
            type="number"
            min={active.start + 0.04}
            max={asset.duration}
            step="0.01"
            value={Number(active.end.toFixed(2))}
            onChange={(e) => setRange(active.start, Number(e.target.value))}
          />
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={() =>
            setRange(clamp(currentTime, 0, active.end - 0.04), active.end)
          }
        >
          Set In <Keys keys="I" />
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() =>
            setRange(
              active.start,
              clamp(currentTime, active.start + 0.04, asset.duration),
            )
          }
        >
          Set Out <Keys keys="O" />
        </Button>
        <span className="ml-auto self-center text-xs text-muted-foreground">
          Cuts stay reversible
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2 border-t bg-muted/40 px-3 py-2">
        <div
          className="flex min-w-0 flex-1 flex-wrap gap-1.5"
          role="group"
          aria-label="Segments"
        >
          {doc.segments.map((s, i) => (
            <button
              key={s.id}
              className={cn(
                "rounded-md border bg-background px-2.5 py-1.5 text-left text-xs shadow-xs transition-colors outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50",
                i === index && "border-primary ring-1 ring-primary",
              )}
              aria-pressed={i === index}
              onClick={() => {
                select(i);
                seek(s.start);
              }}
            >
              <span className="block text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                Segment {i + 1}
              </span>
              <span className="block tabular-nums">
                {timecode(s.start)} – {timecode(s.end)}
              </span>
            </button>
          ))}
        </div>
        <div className="flex items-center">
          <ToolButton
            label="Move segment earlier"
            size="icon-sm"
            disabled={index === 0}
            onClick={() => move(-1)}
          >
            <ArrowLeft />
          </ToolButton>
          <ToolButton
            label="Move segment later"
            size="icon-sm"
            disabled={index >= doc.segments.length - 1}
            onClick={() => move(1)}
          >
            <ArrowRight />
          </ToolButton>
          <ToolButton
            label="Duplicate selected segment"
            size="icon-sm"
            onClick={() =>
              update((d) => ({
                ...d,
                segments: [
                  ...d.segments.slice(0, index + 1),
                  { ...active, id: uid() },
                  ...d.segments.slice(index + 1),
                ],
              }))
            }
          >
            <Copy />
          </ToolButton>
          <ToolButton
            label="Remove selected segment"
            size="icon-sm"
            className="hover:text-destructive"
            disabled={doc.segments.length === 1}
            onClick={() => {
              update((d) => ({
                ...d,
                segments: d.segments.filter((s) => s.id !== active.id),
              }));
              select(Math.max(0, index - 1));
            }}
          >
            <Trash2 />
          </ToolButton>
        </div>
      </div>
    </Panel>
  );
}
