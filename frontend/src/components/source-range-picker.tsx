"use client";

import { useEffect, useId, useState } from "react";
import * as SliderPrimitive from "@radix-ui/react-slider";
import { useTranslations } from "next-intl";

import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  formatClock,
  moveSourceRange,
  shiftStart,
  parseClock,
  sourceRangeProblem,
  type SourceRange,
  type SourceRangeLimits,
} from "@/lib/source-range";

type SourceRangePickerProps = SourceRangeLimits & {
  value: SourceRange;
  onChange: (range: SourceRange) => void;
  disabled?: boolean;
  className?: string;
};

/**
 * Picks the part of a long video to clip: a two-thumb slider for a rough pick and
 * start/end fields for an exact one. Arrow keys move a thumb by a step, Page Up/Down
 * by ten, and the window never grows past the plan's per-video limit.
 */
export function SourceRangePicker({ durationSeconds, maxSeconds, minSeconds, value, onChange, disabled, className }: SourceRangePickerProps) {
  const t = useTranslations("home.sourceRange");
  const id = useId();
  const limits = { durationSeconds, maxSeconds, minSeconds };
  const duration = Math.floor(durationSeconds);
  // A minute per arrow press on long streams; the fields take exact seconds.
  const step = duration > 1800 ? 60 : 5;
  const problem = sourceRangeProblem(value, limits);
  const maxMinutes = Math.round(maxSeconds / 60);
  const lastStart = Math.max(0, duration - Math.min(duration, maxSeconds));

  return (
    <div className={cn("space-y-4", className)}>
      <div className="space-y-2">
        <SliderPrimitive.Root
          className="relative flex h-6 w-full touch-none select-none items-center"
          min={0}
          max={duration}
          step={step}
          minStepsBetweenThumbs={1}
          value={[value.start, value.end]}
          onValueChange={([start, end]) => onChange(moveSourceRange(value, { start, end }, limits))}
          disabled={disabled}
        >
          <SliderPrimitive.Track className="relative h-2 w-full grow overflow-hidden rounded-full bg-muted">
            <SliderPrimitive.Range className="absolute h-full bg-brand" />
          </SliderPrimitive.Track>
          {(["start", "end"] as const).map((thumb) => (
            <SliderPrimitive.Thumb
              key={thumb}
              aria-label={t(thumb)}
              aria-valuetext={formatClock(thumb === "start" ? value.start : value.end)}
              className="block size-6 rounded-full border-2 border-brand bg-background shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 sm:size-5"
            />
          ))}
        </SliderPrimitive.Root>
        <div className="flex justify-between text-[11px] tabular-nums text-muted-foreground" aria-hidden>
          <span>{formatClock(0)}</span>
          <span>{formatClock(duration)}</span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <ClockField id={`${id}-start`} label={t("start")} seconds={value.start} disabled={disabled}
          onCommit={(start) => onChange(shiftStart(value, start, limits))} invalidHint={t("format")} />
        <ClockField id={`${id}-end`} label={t("end")} seconds={value.end} disabled={disabled}
          onCommit={(end) => onChange({ start: value.start, end })} invalidHint={t("format")} />
      </div>

      <div className="flex flex-wrap gap-2">
        <PresetButton disabled={disabled} onClick={() => onChange({ start: 0, end: Math.min(duration, maxSeconds) })}>
          {t("first", { minutes: maxMinutes })}
        </PresetButton>
        <PresetButton disabled={disabled} onClick={() => onChange({ start: lastStart, end: duration })}>
          {t("last", { minutes: maxMinutes })}
        </PresetButton>
      </div>

      <p className={cn("text-sm", problem ? "text-red-600 dark:text-red-400" : "text-muted-foreground")} role={problem ? "alert" : "status"} aria-live="polite">
        {problem
          ? t(`problems.${problem}`, { max: maxMinutes, min: Math.round((minSeconds ?? 60) / 60), duration: formatClock(duration) })
          : t("selected", { length: formatClock(value.end - value.start), minutes: Math.ceil((value.end - value.start) / 60) })}
      </p>
    </div>
  );
}

function PresetButton({ children, onClick, disabled }: { children: React.ReactNode; onClick: () => void; disabled?: boolean }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled}
      className="min-h-9 rounded-full border px-3 text-xs font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
      {children}
    </button>
  );
}

function ClockField({ id, label, seconds, onCommit, disabled, invalidHint }: {
  id: string;
  label: string;
  seconds: number;
  onCommit: (seconds: number) => void;
  disabled?: boolean;
  invalidHint: string;
}) {
  const [draft, setDraft] = useState(formatClock(seconds));
  const [invalid, setInvalid] = useState(false);
  // The slider and the presets move the value too; show theirs unless the user is mid-edit.
  useEffect(() => {
    setDraft(formatClock(seconds));
    setInvalid(false);
  }, [seconds]);

  const commit = () => {
    const parsed = parseClock(draft);
    if (parsed === null) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    if (parsed === seconds) setDraft(formatClock(seconds));
    else onCommit(parsed);
  };

  return (
    <div className="space-y-1">
      <label htmlFor={id} className="text-xs font-medium">{label}</label>
      <Input
        id={id}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            // Enter sets the time; it doesn't submit the form underneath.
            event.preventDefault();
            commit();
          }
        }}
        placeholder="0:00:00"
        autoComplete="off"
        spellCheck={false}
        enterKeyHint="done"
        disabled={disabled}
        aria-invalid={invalid}
        aria-describedby={invalid ? `${id}-hint` : undefined}
        className="h-10 font-mono tabular-nums"
      />
      {invalid && <p id={`${id}-hint`} className="text-xs text-red-600 dark:text-red-400">{invalidHint}</p>}
    </div>
  );
}
