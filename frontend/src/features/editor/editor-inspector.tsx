"use client";

import {
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
} from "react";
import {
  Blend,
  Clock3,
  Copy,
  Crop,
  Eye,
  EyeOff,
  Gauge,
  Image as ImageIcon,
  Lock,
  LockOpen,
  SlidersHorizontal,
  Trash2,
  Type,
  Volume2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import type { EditorProject, TimelineItem, TimelineTrack } from "./types";

interface EditorInspectorProps {
  className?: string;
  project: EditorProject;
  selectedItem: TimelineItem | null;
  onUpdateItem: (itemId: string, updates: Partial<TimelineItem>) => void;
  onUpdateProject: (updates: Partial<EditorProject>) => void;
  onBeginTransform: () => void;
  onEndTransform: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}

function Section({ title, icon, children, className }: { title: string; icon: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("border-b border-white/10 p-3", className)}>
      <h3 className="mb-3 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-zinc-500">
        {icon}
        {title}
      </h3>
      {children}
    </section>
  );
}

function NumericField({
  label,
  value,
  min,
  max,
  step = 1,
  suffix,
  onChange,
}: {
  label: string;
  value: number;
  min?: number;
  max?: number;
  step?: number;
  suffix?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="space-y-1">
      <span className="block text-[10px] font-medium text-zinc-500">{label}</span>
      <div className="relative">
        <Input
          type="number"
          value={Number.isFinite(value) ? Number(value.toFixed(2)) : 0}
          min={min}
          max={max}
          step={step}
          className="h-8 border-white/10 bg-white/5 pr-7 font-mono text-xs text-zinc-200 focus-visible:border-white/25 focus-visible:ring-white/10"
          onChange={(event) => onChange(Number(event.target.value))}
        />
        {suffix ? <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-[9px] text-zinc-600">{suffix}</span> : null}
      </div>
    </label>
  );
}

function RangeField({
  label,
  value,
  min,
  max,
  step = 1,
  suffix = "",
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  suffix?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block space-y-2">
      <span className="flex items-center justify-between text-[10px] font-medium text-zinc-500">
        {label}
        <span className="font-mono text-zinc-300">{Number(value.toFixed(2))}{suffix}</span>
      </span>
      <Slider min={min} max={max} step={step} value={[value]} onValueChange={(next) => onChange(next[0] ?? value)} />
    </label>
  );
}

function ColorField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="space-y-1">
      <span className="block text-[10px] font-medium text-zinc-500">{label}</span>
      <div className="flex h-8 items-center gap-2 rounded-md border border-white/10 bg-white/5 px-2">
        <input
          type="color"
          value={value.startsWith("#") && value.length === 7 ? value : "#ffffff"}
          className="size-5 cursor-pointer appearance-none overflow-hidden rounded border-0 bg-transparent p-0"
          onChange={(event) => onChange(event.target.value)}
        />
        <Input
          value={value}
          className="h-6 border-0 bg-transparent px-0 font-mono text-[10px] text-zinc-300 shadow-none focus-visible:ring-0"
          onChange={(event) => onChange(event.target.value)}
        />
      </div>
    </label>
  );
}

const TRACK_LABELS: Record<TimelineTrack, string> = {
  main: "Main video",
  overlay: "Overlay",
  text: "Text",
  audio: "Audio",
};

export function EditorInspector({
  className,
  project,
  selectedItem,
  onUpdateItem,
  onUpdateProject,
  onBeginTransform,
  onEndTransform,
  onDuplicate,
  onDelete,
}: EditorInspectorProps) {
  const sliderCleanupRef = useRef<(() => void) | null>(null);
  const beginSliderTransaction = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target?.closest('[data-slot="slider"]') || sliderCleanupRef.current) return;
    onBeginTransform();
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
      window.removeEventListener("blur", finish);
      sliderCleanupRef.current = null;
      onEndTransform();
    };
    sliderCleanupRef.current = finish;
    window.addEventListener("pointerup", finish, { once: true });
    window.addEventListener("pointercancel", finish, { once: true });
    window.addEventListener("blur", finish, { once: true });
  }, [onBeginTransform, onEndTransform]);

  useEffect(() => () => sliderCleanupRef.current?.(), []);

  if (!selectedItem) {
    const canvas = project.canvas;
    return (
      <aside onPointerDownCapture={beginSliderTransaction} className={cn("flex h-full min-h-0 w-[300px] shrink-0 flex-col border-l border-white/10 bg-[#141414] text-white", className)}>
        <div className="flex h-12 shrink-0 items-center border-b border-white/10 px-4">
          <h2 className="text-sm font-semibold">Project</h2>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <Section title="Canvas" icon={<ImageIcon className="size-3.5" />}>
            <div className="grid grid-cols-3 gap-1.5">
              {[
                { label: "9:16", width: 1080, height: 1920 },
                { label: "1:1", width: 1080, height: 1080 },
                { label: "16:9", width: 1920, height: 1080 },
              ].map((preset) => {
                const active = canvas.width === preset.width && canvas.height === preset.height;
                return (
                  <button
                    key={preset.label}
                    type="button"
                    className={cn(
                      "h-9 rounded-md border text-[10px] font-semibold transition",
                      active
                        ? "border-white/40 bg-white/10 text-white"
                        : "border-white/10 bg-white/5 text-zinc-400 hover:bg-white/10",
                    )}
                    onClick={() => onUpdateProject({ canvas: { ...canvas, width: preset.width, height: preset.height } })}
                  >
                    {preset.label}
                  </button>
                );
              })}
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <NumericField label="Width" value={canvas.width} min={240} max={3840} onChange={(width) => onUpdateProject({ canvas: { ...canvas, width } })} />
              <NumericField label="Height" value={canvas.height} min={240} max={3840} onChange={(height) => onUpdateProject({ canvas: { ...canvas, height } })} />
            </div>
            <div className="mt-3">
              <ColorField label="Background" value={canvas.background} onChange={(background) => onUpdateProject({ canvas: { ...canvas, background } })} />
            </div>
          </Section>
          <Section title="Playback" icon={<Clock3 className="size-3.5" />}>
            <div className="grid grid-cols-2 gap-2">
              <NumericField label="Duration" value={project.duration} min={0.5} max={3600} step={0.1} suffix="s" onChange={(duration) => onUpdateProject({ duration })} />
              <NumericField label="Frame rate" value={canvas.fps} min={12} max={60} suffix="fps" onChange={(fps) => onUpdateProject({ canvas: { ...canvas, fps } })} />
            </div>
          </Section>
          <div className="p-4 text-[11px] leading-5 text-zinc-500">
            Select any layer on the canvas or timeline to edit its transform, crop, timing, audio, and styling.
          </div>
        </div>
      </aside>
    );
  }

  const update = (updates: Partial<TimelineItem>) => onUpdateItem(selectedItem.id, updates);

  return (
    <aside onPointerDownCapture={beginSliderTransaction} className={cn("flex h-full min-h-0 w-[300px] shrink-0 flex-col border-l border-white/10 bg-[#141414] text-white", className)}>
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-white/10 px-3">
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-semibold">{selectedItem.name}</div>
          <div className="text-[9px] uppercase tracking-[0.14em] text-zinc-600">{selectedItem.type} layer</div>
        </div>
        <Button type="button" variant="ghost" size="icon-sm" title="Duplicate" className="text-zinc-500 hover:bg-white/10 hover:text-white" onClick={onDuplicate}>
          <Copy />
        </Button>
        <Button type="button" variant="ghost" size="icon-sm" title="Delete" className="text-zinc-500 hover:bg-red-500/15 hover:text-red-300" onClick={onDelete}>
          <Trash2 />
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <Section title="Layer" icon={<SlidersHorizontal className="size-3.5" />}>
          <div className="grid grid-cols-[1fr_auto_auto] gap-2">
            <select
              value={selectedItem.track}
              className="h-8 rounded-md border border-white/10 bg-white/5 px-2 text-xs text-zinc-300 outline-none focus:border-white/25"
              onChange={(event) => update({ track: event.target.value as TimelineTrack })}
            >
              {(Object.keys(TRACK_LABELS) as TimelineTrack[]).map((track) => <option key={track} value={track}>{TRACK_LABELS[track]}</option>)}
            </select>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              title={selectedItem.hidden ? "Show layer" : "Hide layer"}
              className="border border-white/10 bg-white/5 text-zinc-400 hover:bg-white/10 hover:text-white"
              onClick={() => update({ hidden: !selectedItem.hidden })}
            >
              {selectedItem.hidden ? <EyeOff /> : <Eye />}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              title={selectedItem.locked ? "Unlock layer" : "Lock layer"}
              className="border border-white/10 bg-white/5 text-zinc-400 hover:bg-white/10 hover:text-white"
              onClick={() => update({ locked: !selectedItem.locked })}
            >
              {selectedItem.locked ? <Lock /> : <LockOpen />}
            </Button>
          </div>
        </Section>

        <Section title="Timing" icon={<Clock3 className="size-3.5" />}>
          <div className="grid grid-cols-2 gap-2">
            <NumericField label="Start" value={selectedItem.start} min={0} max={project.duration} step={0.05} suffix="s" onChange={(start) => update({ start })} />
            <NumericField label="Duration" value={selectedItem.duration} min={0.1} max={project.duration} step={0.05} suffix="s" onChange={(duration) => update({ duration })} />
            {selectedItem.assetId ? (
              <NumericField label="Source in" value={selectedItem.trimStart} min={0} step={0.05} suffix="s" onChange={(trimStart) => update({ trimStart })} />
            ) : null}
            {(selectedItem.type === "video" || selectedItem.type === "audio") ? (
              <NumericField label="Speed" value={selectedItem.speed} min={0.25} max={4} step={0.05} suffix="×" onChange={(speed) => update({ speed })} />
            ) : null}
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <NumericField label="Fade in" value={selectedItem.fadeIn} min={0} max={selectedItem.duration / 2} step={0.1} suffix="s" onChange={(fadeIn) => update({ fadeIn })} />
            <NumericField label="Fade out" value={selectedItem.fadeOut} min={0} max={selectedItem.duration / 2} step={0.1} suffix="s" onChange={(fadeOut) => update({ fadeOut })} />
          </div>
        </Section>

        {selectedItem.type !== "audio" ? (
          <Section title="Transform" icon={<Gauge className="size-3.5" />}>
            <div className="grid grid-cols-2 gap-2">
              <NumericField label="X" value={selectedItem.transform.x} step={0.1} suffix="%" onChange={(x) => update({ transform: { ...selectedItem.transform, x } })} />
              <NumericField label="Y" value={selectedItem.transform.y} step={0.1} suffix="%" onChange={(y) => update({ transform: { ...selectedItem.transform, y } })} />
              <NumericField label="Width" value={selectedItem.transform.width} min={1} step={0.1} suffix="%" onChange={(width) => update({ transform: { ...selectedItem.transform, width } })} />
              <NumericField label="Height" value={selectedItem.transform.height} min={1} step={0.1} suffix="%" onChange={(height) => update({ transform: { ...selectedItem.transform, height } })} />
            </div>
            <div className="mt-3 space-y-3">
              <RangeField label="Rotation" value={selectedItem.transform.rotation} min={-180} max={180} suffix="°" onChange={(rotation) => update({ transform: { ...selectedItem.transform, rotation } })} />
              <RangeField label="Opacity" value={selectedItem.opacity} min={0} max={100} suffix="%" onChange={(opacity) => update({ opacity })} />
            </div>
          </Section>
        ) : null}

        {(selectedItem.type === "video" || selectedItem.type === "image") ? (
          <Section title="Crop" icon={<Crop className="size-3.5" />}>
            <div className="grid grid-cols-2 gap-2">
              {(["left", "right", "top", "bottom"] as const).map((edge) => (
                <NumericField
                  key={edge}
                  label={edge[0].toUpperCase() + edge.slice(1)}
                  value={selectedItem.crop[edge]}
                  min={0}
                  max={90}
                  step={0.5}
                  suffix="%"
                  onChange={(value) => update({ crop: { ...selectedItem.crop, [edge]: value } })}
                />
              ))}
            </div>
            <Button type="button" variant="ghost" size="sm" className="mt-2 w-full border border-white/10 bg-white/5 text-xs text-zinc-400 hover:bg-white/10 hover:text-white" onClick={() => update({ crop: { top: 0, right: 0, bottom: 0, left: 0 } })}>
              Reset crop
            </Button>
          </Section>
        ) : null}

        {(selectedItem.type === "video" || selectedItem.type === "image") ? (
          <Section title="Adjust" icon={<Blend className="size-3.5" />}>
            <div className="space-y-3">
              <RangeField label="Brightness" value={selectedItem.effects.brightness} min={0} max={200} suffix="%" onChange={(brightness) => update({ effects: { ...selectedItem.effects, brightness } })} />
              <RangeField label="Contrast" value={selectedItem.effects.contrast} min={0} max={200} suffix="%" onChange={(contrast) => update({ effects: { ...selectedItem.effects, contrast } })} />
              <RangeField label="Saturation" value={selectedItem.effects.saturation} min={0} max={200} suffix="%" onChange={(saturation) => update({ effects: { ...selectedItem.effects, saturation } })} />
              <RangeField label="Blur" value={selectedItem.effects.blur} min={0} max={20} step={0.1} suffix="px" onChange={(blur) => update({ effects: { ...selectedItem.effects, blur } })} />
              <RangeField label="Hue" value={selectedItem.effects.hue} min={-180} max={180} suffix="°" onChange={(hue) => update({ effects: { ...selectedItem.effects, hue } })} />
            </div>
          </Section>
        ) : null}

        {(selectedItem.type === "video" || selectedItem.type === "audio") ? (
          <Section title="Audio" icon={<Volume2 className="size-3.5" />}>
            <RangeField label="Volume" value={selectedItem.volume} min={0} max={100} suffix="%" onChange={(volume) => update({ volume })} />
            <Button type="button" variant="ghost" size="sm" className="mt-3 w-full border border-white/10 bg-white/5 text-xs text-zinc-400 hover:bg-white/10 hover:text-white" onClick={() => update({ muted: !selectedItem.muted })}>
              {selectedItem.muted ? "Unmute layer" : "Mute layer"}
            </Button>
          </Section>
        ) : null}

        {(selectedItem.type === "text" || selectedItem.type === "caption") && selectedItem.text ? (
          <Section title="Text" icon={<Type className="size-3.5" />}>
            <Textarea
              value={selectedItem.text.content}
              className="select-text min-h-20 resize-y border-white/10 bg-white/5 text-xs text-zinc-200 focus-visible:border-white/25 focus-visible:ring-white/10"
              onChange={(event) => update({ text: { ...selectedItem.text!, content: event.target.value } })}
            />
            <div className="mt-3 grid grid-cols-2 gap-2">
              <label className="space-y-1">
                <span className="block text-[10px] font-medium text-zinc-500">Font</span>
                <select
                  value={selectedItem.text.fontFamily}
                  className="h-8 w-full rounded-md border border-white/10 bg-white/5 px-2 text-xs text-zinc-300 outline-none focus:border-white/25"
                  onChange={(event) => update({ text: { ...selectedItem.text!, fontFamily: event.target.value } })}
                >
                  <option value="Inter">Inter</option>
                  <option value="Arial">Arial</option>
                  <option value="Georgia">Georgia</option>
                  <option value="Impact">Impact</option>
                  <option value="monospace">Mono</option>
                </select>
              </label>
              <NumericField label="Size" value={selectedItem.text.fontSize} min={8} max={240} suffix="px" onChange={(fontSize) => update({ text: { ...selectedItem.text!, fontSize } })} />
              <NumericField label="Weight" value={selectedItem.text.fontWeight} min={100} max={900} step={100} onChange={(fontWeight) => update({ text: { ...selectedItem.text!, fontWeight } })} />
              <NumericField label="Spacing" value={selectedItem.text.letterSpacing} min={-10} max={30} step={0.5} onChange={(letterSpacing) => update({ text: { ...selectedItem.text!, letterSpacing } })} />
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <ColorField label="Text" value={selectedItem.text.color} onChange={(color) => update({ text: { ...selectedItem.text!, color } })} />
              <ColorField label="Stroke" value={selectedItem.text.strokeColor} onChange={(strokeColor) => update({ text: { ...selectedItem.text!, strokeColor } })} />
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <NumericField label="Stroke" value={selectedItem.text.strokeWidth} min={0} max={12} step={0.5} suffix="px" onChange={(strokeWidth) => update({ text: { ...selectedItem.text!, strokeWidth } })} />
              <NumericField label="Line height" value={selectedItem.text.lineHeight} min={0.7} max={3} step={0.05} onChange={(lineHeight) => update({ text: { ...selectedItem.text!, lineHeight } })} />
            </div>
            <div className="mt-3 grid grid-cols-3 gap-1.5">
              {(["left", "center", "right"] as const).map((align) => (
                <button
                  key={align}
                  type="button"
                  className={cn(
                    "h-8 rounded-md border text-[10px] capitalize",
                    selectedItem.text?.align === align
                      ? "border-white/40 bg-white/10 text-white"
                      : "border-white/10 bg-white/5 text-zinc-500",
                  )}
                  onClick={() => update({ text: { ...selectedItem.text!, align } })}
                >
                  {align}
                </button>
              ))}
            </div>
          </Section>
        ) : null}

        {selectedItem.type === "shape" && selectedItem.shape ? (
          <Section title="Shape" icon={<ImageIcon className="size-3.5" />}>
            <div className="grid grid-cols-2 gap-2">
              <ColorField label="Fill" value={selectedItem.shape.fill} onChange={(fill) => update({ shape: { ...selectedItem.shape!, fill } })} />
              <NumericField label="Corners" value={selectedItem.shape.borderRadius} min={0} max={999} suffix="px" onChange={(borderRadius) => update({ shape: { ...selectedItem.shape!, borderRadius } })} />
            </div>
          </Section>
        ) : null}
      </div>
    </aside>
  );
}
