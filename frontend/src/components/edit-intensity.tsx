"use client";

// The create screen's "Edits" settings: how hard the AI-tagged moments hit (intensity), and what it may use to hit them.
import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from "react";
import { useTranslations } from "next-intl";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { INTENSITIES, type Censor, type EditStyle, type Intensity } from "@/lib/fx/core/types";
import { CENSORS, EDIT_STYLES_UNAVAILABLE, fetchEditStylesInfo, type EditStylesInfo } from "@/lib/fx/edit-style-request";
import { cn } from "@/lib/utils";

/** `GET /api/tasks/edit-styles` once per mount; null until it answers, "not enabled" on any failure. */
export function useEditStylesInfo(active = true): EditStylesInfo | null {
  const [info, setInfo] = useState<EditStylesInfo | null>(null);
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    void fetchEditStylesInfo().then((next) => { if (!cancelled) setInfo(next); }, () => { if (!cancelled) setInfo(EDIT_STYLES_UNAVAILABLE); });
    return () => { cancelled = true; };
  }, [active]);
  return info;
}

// Five bars per glyph: resting height (0-1) and how far each one swings, per intensity.
const METER: Record<Intensity, { rest: number[]; swing: number; duration: number; jitter: boolean }> = {
  off: { rest: [0.18, 0.18, 0.18, 0.18, 0.18], swing: 0, duration: 0, jitter: false },
  subtle: { rest: [0.3, 0.45, 0.38, 0.5, 0.32], swing: 0.25, duration: 2.2, jitter: false },
  bold: { rest: [0.45, 0.7, 0.55, 0.8, 0.5], swing: 0.45, duration: 1.0, jitter: false },
  max: { rest: [0.7, 0.95, 0.6, 1, 0.8], swing: 0.65, duration: 0.42, jitter: true },
};

const METER_CSS = `
@keyframes fx-meter-bar { 0%, 100% { transform: scaleY(var(--fx-rest)); } 50% { transform: scaleY(var(--fx-peak)); } }
@keyframes fx-meter-shake { 0%, 100% { transform: translate(0, 0) rotate(0); } 25% { transform: translate(-0.5px, 0.5px) rotate(-2deg); } 75% { transform: translate(0.5px, -0.5px) rotate(2deg); } }
.fx-meter-bar { transform: scaleY(var(--fx-rest)); transform-origin: bottom; }
@media (prefers-reduced-motion: no-preference) {
  .fx-meter[data-animated="true"] .fx-meter-bar { animation: fx-meter-bar var(--fx-duration) ease-in-out infinite; animation-delay: var(--fx-delay); }
  .fx-meter[data-jitter="true"] { animation: fx-meter-shake 0.18s linear infinite; }
}`;

/** A tiny level meter that moves like the intensity: still when off, a slow breath for subtle, chaos for max. */
function IntensityGlyph({ intensity, active }: { intensity: Intensity; active: boolean }) {
  const meter = METER[intensity];
  return (
    <span
      aria-hidden
      className={cn("fx-meter flex h-7 items-end gap-[3px]", active ? "text-brand" : "text-foreground/55")}
      data-animated={meter.swing > 0}
      data-jitter={meter.jitter}
    >
      {meter.rest.map((rest, index) => (
        <span
          key={index}
          className="fx-meter-bar block h-full w-[4px] rounded-full bg-current"
          style={{
            "--fx-rest": rest,
            "--fx-peak": Math.min(1, rest + meter.swing),
            "--fx-duration": `${meter.duration}s`,
            "--fx-delay": `${-(index * 0.37 * meter.duration) % Math.max(meter.duration, 0.01)}s`,
          } as CSSProperties}
        />
      ))}
    </span>
  );
}

function OptionRow({ id, label, hint, checked, onChange, disabled, children }: {
  id: string;
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  children?: ReactNode;
}) {
  return (
    <div className="py-2.5">
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <label htmlFor={id} className="text-sm font-medium">{label}</label>
          {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
        </div>
        <Switch id={id} checked={checked} onCheckedChange={onChange} disabled={disabled} />
      </div>
      {children}
    </div>
  );
}

const BOOL_OPTIONS = {
  sound: ["sfx", "music"],
  motion: ["zooms", "shake", "transitions", "speed"],
  retention: ["wait_for_it", "progress_bar", "loop_ending"],
} as const;

/**
 * Intensity as four radio cards (`data-tour="edit-intensity"`) and, once edits are on, the switches for what they use.
 * Off sends nothing new: the request stays exactly what it was before dynamic edits.
 */
export function EditIntensity({ value, onChange, info, disabled }: {
  value: EditStyle;
  onChange: (style: EditStyle) => void;
  info: EditStylesInfo;
  disabled?: boolean;
}) {
  const t = useTranslations("home");
  const cards = useRef(new Map<Intensity, HTMLButtonElement>());
  const intensities = INTENSITIES.filter((level) => info.intensities.includes(level));
  const set = <K extends keyof EditStyle>(key: K, next: EditStyle[K]) => onChange({ ...value, [key]: next });
  const active = value.intensity !== "off";

  const move = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = intensities[(index + step + intensities.length) % intensities.length];
    set("intensity", next);
    cards.current.get(next)?.focus();
  };

  const toggle = (key: (typeof BOOL_OPTIONS)[keyof typeof BOOL_OPTIONS][number]) => (
    <OptionRow
      key={key}
      id={`edit-${key}`}
      label={t(`edits.options.${key}.label`)}
      hint={t(`edits.options.${key}.hint`)}
      checked={value[key]}
      onChange={(checked) => set(key, checked)}
      disabled={disabled}
    >
      {key === "music" && value.music && (
        <Select value={value.music_mood} onValueChange={(mood) => set("music_mood", mood as EditStyle["music_mood"])} disabled={disabled}>
          <SelectTrigger size="sm" className="mt-2 w-full" aria-label={t("edits.options.musicMood")}><SelectValue /></SelectTrigger>
          <SelectContent>
            {(["auto", ...info.music_moods] as const).map((mood) => (
              <SelectItem key={mood} value={mood}>{t(`edits.moods.${mood}`)}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </OptionRow>
  );

  return (
    <div className="space-y-4">
      <style>{METER_CSS}</style>
      <div>
        <p className="text-sm font-medium">{t("edits.title")}</p>
        <p className="text-xs text-muted-foreground">{t("edits.body")}</p>
      </div>
      <div role="radiogroup" aria-label={t("edits.title")} data-tour="edit-intensity" className="grid grid-cols-2 gap-2">
        {intensities.map((level, index) => {
          const checked = value.intensity === level;
          return (
            <button
              key={level}
              ref={(node) => { if (node) cards.current.set(level, node); else cards.current.delete(level); }}
              type="button"
              role="radio"
              aria-checked={checked}
              tabIndex={checked || (!intensities.includes(value.intensity) && index === 0) ? 0 : -1}
              disabled={disabled}
              onClick={() => set("intensity", level)}
              onKeyDown={(event) => move(event, index)}
              className={cn(
                "flex min-w-0 items-start gap-3 rounded-xl border p-3 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
                checked && "border-foreground ring-1 ring-foreground",
              )}
            >
              <IntensityGlyph intensity={level} active={checked && level !== "off"} />
              <span className="min-w-0">
                <span className="block text-sm font-medium">{t(`edits.intensity.${level}.label`)}</span>
                <span className="block text-xs text-muted-foreground">{t(`edits.intensity.${level}.hint`)}</span>
              </span>
            </button>
          );
        })}
      </div>

      {active ? (
        <div className="space-y-4">
          <section aria-labelledby="edits-sound">
            <p id="edits-sound" className="text-xs font-medium text-muted-foreground">{t("edits.groups.sound")}</p>
            <div className="divide-y">
              {BOOL_OPTIONS.sound.map(toggle)}
              <div className="py-2.5">
                <p id="edit-censor-label" className="text-sm font-medium">{t("edits.options.censor.label")}</p>
                <p className="text-xs text-muted-foreground">{t("edits.options.censor.hint")}</p>
                <Select value={value.censor} onValueChange={(censor) => set("censor", censor as Censor)} disabled={disabled}>
                  <SelectTrigger size="sm" className="mt-2 w-full" aria-labelledby="edit-censor-label"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {CENSORS.map((censor) => <SelectItem key={censor} value={censor}>{t(`edits.censor.${censor}`)}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </section>
          <section aria-labelledby="edits-motion">
            <p id="edits-motion" className="text-xs font-medium text-muted-foreground">{t("edits.groups.motion")}</p>
            <div className="divide-y">{BOOL_OPTIONS.motion.map(toggle)}</div>
          </section>
          <section aria-labelledby="edits-retention">
            <p id="edits-retention" className="text-xs font-medium text-muted-foreground">{t("edits.groups.retention")}</p>
            <div className="divide-y">
              {BOOL_OPTIONS.retention.map(toggle)}
              {info.reactions_available && (
                <OptionRow
                  id="edit-reactions"
                  label={t("edits.options.reactions.label")}
                  hint={t("edits.options.reactions.hint")}
                  checked={value.reactions}
                  onChange={(checked) => set("reactions", checked)}
                  disabled={disabled}
                />
              )}
            </div>
          </section>
        </div>
      ) : (
        <p className="rounded-lg bg-muted/60 px-3 py-2.5 text-xs text-muted-foreground">{t("edits.offHint")}</p>
      )}
    </div>
  );
}
