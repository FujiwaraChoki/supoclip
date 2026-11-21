"use client";
import { useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import {
  DEFAULT_DOC_FX,
  clamp,
  editDuration,
  fxWarp,
  locate,
  mappedFx,
  timecode,
  uid,
  type EditDocument,
} from "@/lib/editor/document";
import { storedCueId } from "@/lib/fx/studio-scene";
import { fnv1a32 } from "@/lib/fx/core/rng";
import { INSTANT_KINDS, type FxCue, type FxKind, type SfxCue, type SoundId } from "@/lib/fx/core/types";
import { toOutput } from "@/lib/fx/core/warp";

export type FxFamily = "sound" | "picture" | "overlay" | "time";
/** Lane rows, top to bottom. */
export const FX_FAMILIES: readonly FxFamily[] = ["picture", "overlay", "sound", "time"];
export const FX_FAMILY: Record<FxKind, FxFamily> = {
  sfx: "sound", bleep: "sound",
  zoom: "picture", shake: "picture", flash: "picture", glitch: "picture", desaturate: "picture", transition: "picture",
  accent: "overlay", sticker: "overlay", reaction: "overlay",
  speed: "time", freeze: "time",
};
const FAMILY_COLOR: Record<FxFamily, string> = {
  picture: "bg-sky-500 dark:bg-sky-400",
  overlay: "bg-violet-500 dark:bg-violet-400",
  sound: "bg-emerald-500 dark:bg-emerald-400",
  time: "bg-rose-500 dark:bg-rose-400",
};
export const SOUND_IDS: readonly SoundId[] = ["whoosh", "impact", "pop", "riser", "record_scratch", "boom",
  "cash_register", "ticker", "slap", "stamp", "glitch", "tape_stop"];
/** == edit_fx.resolver.SFX_TRIM_DB: per-sound trims the resolver and AI edits give a new sound cue. */
const SFX_TRIM_DB: Partial<Record<SoundId, number>> = { ticker: -6, riser: -4, whoosh: -2, glitch: -3, tape_stop: -2 };

/** Row height and gap of the lane, px. Four rows: 4 * 9 + 3 * 1 = 39 (h-10). */
const ROW = 9, GAP = 1;
export const FX_LANE_HEIGHT = 40;

/** The stored cue a mapped piece came from: `@k` (repeat) suffixes stripped. */
export const cueStoredId = storedCueId;
/** A span the server split at a junction is stored as `id`, `id~1`, ...: they are one cue to the user. */
const cueGroup = (id: string) => id.split("~")[0];

/**
 * Delete a cue (stored id). Its pieces split at junctions go with it. An auto cue's id goes to `suppressed` so a
 * re-resolve never brings it back; a user cue is just removed.
 */
export function removeFxCue(doc: EditDocument, id: string): EditDocument {
  const fx = doc.fx;
  if (!fx) return doc;
  const group = cueGroup(id);
  const removed = fx.cues.filter((cue) => cueGroup(cue.id) === group);
  if (!removed.length) return doc;
  const auto = removed.some((cue) => cue.origin === "auto");
  return {
    ...doc,
    fx: {
      ...fx,
      cues: fx.cues.filter((cue) => cueGroup(cue.id) !== group),
      suppressed: auto && !fx.suppressed.includes(group) ? [...fx.suppressed, group] : fx.suppressed,
    },
  };
}

/** Shift a stored cue by `seconds` of source time (kept inside the source), making it the user's own. */
export function moveFxCue(doc: EditDocument, id: string, seconds: number, sourceDuration: number): EditDocument {
  const fx = doc.fx;
  const cue = fx?.cues.find((c) => c.id === id);
  if (!fx || !cue) return doc;
  const length = cue.end - cue.start;
  const start = clamp(cue.start + seconds, 0, Math.max(0, sourceDuration - length));
  const round = (v: number) => Math.round(v * 1000) / 1000;
  const moved = { ...cue, start: round(start), end: round(start + length), origin: "user" as const };
  return { ...doc, fx: { ...fx, cues: fx.cues.map((c) => (c.id === id ? moved : c)).sort(byStartThenId) } };
}

const byStartThenId = (a: FxCue, b: FxCue) => a.start - b.start || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

/** `u0`, `u1`, ...: the first free user id (== ai_edit_service._next_id). */
export function nextUserCueId(cues: readonly FxCue[]): string {
  const taken = new Set(cues.map((cue) => cue.id));
  let n = 0;
  while (taken.has(`u${n}`)) n += 1;
  return `u${n}`;
}

/** A user sound at a source instant, built like the AI edit's `add_effect` sfx cue. */
export function addUserSound(doc: EditDocument, sourceTime: number, sound: SoundId): EditDocument {
  const fx = doc.fx ?? DEFAULT_DOC_FX;
  if (fx.cues.length >= 400) return doc;
  const id = nextUserCueId(fx.cues);
  const start = Math.round(Math.max(0, sourceTime) * 1000) / 1000;
  const cue: SfxCue = {
    id, kind: "sfx", start, end: start, seed: fnv1a32(`${fx.seed}:${id}:sfx`), beat: "", origin: "user",
    sound, variant: 0, gain_db: SFX_TRIM_DB[sound] ?? 0,
  };
  return { ...doc, fx: { ...fx, cues: [...fx.cues, cue].sort(byStartThenId) } };
}

interface Props {
  doc: EditDocument;
  /** Selected cue (stored id). */
  selected: string | null;
  select: (id: string | null) => void;
  /** Open a cue in the inspector (double-click / Enter). */
  open: (id: string) => void;
  seekOut: (time: number) => void;
  update: (change: (doc: EditDocument) => EditDocument, group?: string) => void;
  at: (clientX: number) => number;
  secondsPerPixel: () => number;
  /** Source duration (clean.mp4). */
  duration: number;
  /** Source time under the playhead, where a picked sound goes. */
  playheadSource: number;
  /** Controlled sound picker (the lane header opens it too). */
  pickerOpen: boolean;
  setPickerOpen: (open: boolean) => void;
}

/**
 * The dynamic-edits lane: every cue of an enabled kind on the edit timeline, one row per family. Instants are
 * diamonds, spans are bars; a freeze carries its hold, a speed ramp is hatched. Click selects and seeks, drag moves
 * (the cue becomes the user's), double-click opens it in the inspector, double-click on empty space adds a sound.
 */
export function FxTrack({
  doc, selected, select, open, seekOut, update, at, secondsPerPixel, duration, playheadSource, pickerOpen, setPickerOpen,
}: Props) {
  const t = useTranslations("editor");
  const total = Math.max(0.01, editDuration(doc));
  const cues = mappedFx(doc);
  const warp = fxWarp(doc);
  const canAddSound = !!doc.fx && !doc.fx.disabled.includes("sfx");
  const laneRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: string; x: number; document: EditDocument; start: number; scale: number; group: string; moved: boolean } | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const pct = (seconds: number) => `${(seconds / total) * 100}%`;
  const label = (cue: FxCue) => {
    const kind = t(`fx.kinds.${cue.kind}`);
    return { kind, time: timecode(toOutput(warp, cue.start)) };
  };

  const begin = (e: React.PointerEvent, cue: FxCue) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    const id = storedCueId(cue.id);
    select(id);
    drag.current = { id, x: e.clientX, document: doc, start: cue.start, scale: secondsPerPixel(), group: `gesture:${uid()}`, moved: false };
    laneRef.current?.setPointerCapture?.(e.pointerId);
  };
  const move = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    if (!(e.buttons & 1)) {
      drag.current = null;
      setDragging(null);
      return;
    }
    if (!d.moved && Math.abs(e.clientX - d.x) <= 3) return;
    if (!d.moved) setDragging(d.id);
    d.moved = true;
    const delta = (e.clientX - d.x) * d.scale;
    const edit = editDuration(d.document);
    const from = locate(d.document, d.start).source;
    const to = locate(d.document, clamp(d.start + delta, 0, edit)).source;
    const moved = moveFxCue(d.document, d.id, to - from, duration);
    update((current) => ({ ...current, fx: moved.fx }), d.group);
  };
  const end = () => {
    const d = drag.current;
    drag.current = null;
    setDragging(null);
    if (d && !d.moved) seekOut(d.start + 0.001);
  };

  return (
    <Popover open={pickerOpen} onOpenChange={setPickerOpen}>
      <PopoverAnchor asChild>
        <div
          ref={laneRef}
          role="group"
          aria-label={t("fxTimeline.fxLaneAria")}
          data-tour="studio-fx-lane"
          title={canAddSound ? t("fxTimeline.addSoundHint") : undefined}
          className="relative rounded-lg bg-stone-50 dark:bg-stone-900/50"
          style={{ height: FX_LANE_HEIGHT }}
          onPointerMove={move}
          onPointerUp={end}
          onPointerCancel={() => { drag.current = null; setDragging(null); }}
          onLostPointerCapture={() => { drag.current = null; setDragging(null); }}
          onPointerDown={(e) => {
            if (e.target !== e.currentTarget || e.button !== 0) return;
            select(null);
            seekOut(at(e.clientX));
          }}
          onDoubleClick={(e) => {
            if (e.target === e.currentTarget && canAddSound) setPickerOpen(true);
          }}
        >
          {FX_FAMILIES.slice(1).map((family, row) => (
            <span
              key={family}
              aria-hidden
              className="pointer-events-none absolute inset-x-0 border-t border-dashed border-stone-200 dark:border-stone-800"
              style={{ top: (row + 1) * (ROW + GAP) - GAP / 2 }}
            />
          ))}
          {cues.map((cue) => {
            const id = storedCueId(cue.id);
            const family = FX_FAMILY[cue.kind];
            const top = FX_FAMILIES.indexOf(family) * (ROW + GAP);
            const instant = INSTANT_KINDS.has(cue.kind) || cue.end - cue.start < 1e-6;
            const isSelected = id === selected;
            const { kind, time } = label(cue);
            const common = {
              role: "button",
              tabIndex: 0,
              "aria-pressed": isSelected,
              "aria-label": t("fxTimeline.cueAria", { kind, time }),
              title: t("fxTimeline.cueTitle", { kind, time }),
              "data-fx-cue": id,
              "data-fx-kind": cue.kind,
              onPointerDown: (e: React.PointerEvent) => begin(e, cue),
              // Tabbing to a cue selects it, so Delete works from the keyboard too.
              onFocus: () => {
                if (id !== selected) select(id);
              },
              onDoubleClick: (e: React.MouseEvent) => {
                e.stopPropagation();
                open(id);
              },
              onKeyDown: (e: React.KeyboardEvent) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  open(id);
                } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
                  // Nudge by a frame (a second with shift) instead of moving the playhead.
                  e.preventDefault();
                  e.stopPropagation();
                  select(id);
                  const step = (e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 1 : 1 / 30);
                  const from = locate(doc, cue.start).source;
                  const to = locate(doc, clamp(cue.start + step, 0, total)).source;
                  update((current) => moveFxCue(current, id, to - from, duration), `fx-nudge:${id}`);
                }
              },
            } as const;
            const ring = cn(
              isSelected && "ring-2 ring-foreground ring-offset-1 ring-offset-background",
              dragging === id && "opacity-80",
            );
            if (instant)
              return (
                <div
                  key={cue.id}
                  {...common}
                  className="absolute z-10 flex -translate-x-[4px] cursor-grab touch-none items-center rounded-sm px-px outline-none focus-visible:ring-2 focus-visible:ring-ring active:cursor-grabbing"
                  style={{ left: pct(cue.start), top, height: ROW }}
                >
                  <span className={cn("block size-[7px] rotate-45 rounded-[1px]", FAMILY_COLOR[family], ring)} />
                  {cue.kind === "freeze" && (
                    <span
                      className={cn(
                        "pointer-events-none absolute top-0 rounded-sm bg-rose-100 px-0.5 font-mono text-[8px] leading-[9px] whitespace-nowrap text-rose-900 tabular-nums dark:bg-rose-950 dark:text-rose-100",
                        // Late in the clip the badge sits before the notch so it never runs off the lane.
                        cue.start / total > 0.8 ? "right-full mr-0.5" : "left-full ml-0.5",
                      )}
                    >
                      {t("fxTimeline.hold", { seconds: cue.hold.toFixed(1) })}
                    </span>
                  )}
                </div>
              );
            return (
              <div
                key={cue.id}
                {...common}
                className={cn("absolute min-w-[3px] cursor-grab touch-none rounded-[3px] outline-none focus-visible:ring-2 focus-visible:ring-ring active:cursor-grabbing", FAMILY_COLOR[family], ring)}
                style={{
                  left: pct(cue.start),
                  width: pct(cue.end - cue.start),
                  top: top + 1,
                  height: ROW - 2,
                  ...(cue.kind === "speed"
                    ? { backgroundImage: "repeating-linear-gradient(135deg, rgb(255 255 255 / 0.55) 0 2px, transparent 2px 5px)" }
                    : {}),
                }}
              >
                {cue.kind === "speed" && (
                  <span className="pointer-events-none absolute -top-px left-1 font-mono text-[8px] leading-[9px] font-semibold text-white tabular-nums">
                    {t("fxTimeline.speed", { rate: Number(cue.rate.toFixed(2)) })}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </PopoverAnchor>
      <PopoverContent align="center" side="top" className="w-64 p-2">
        <p className="px-1 pb-1.5 text-xs font-semibold">{t("fxTimeline.pickSound")}</p>
        <div className="grid grid-cols-2 gap-1">
          {SOUND_IDS.map((sound) => (
            <button
              key={sound}
              type="button"
              className="rounded-md px-2 py-1.5 text-left text-xs hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
              onClick={() => {
                update((current) => addUserSound(current, playheadSource, sound));
                select(nextUserCueId(doc.fx?.cues ?? []));
                setPickerOpen(false);
              }}
            >
              {t(`fx.sounds.${sound}`)}
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
