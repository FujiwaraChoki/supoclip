"use client";
import type { ComponentProps } from "react";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import { editDuration, fxWarp, mappedWords, type EditDocument } from "@/lib/editor/document";
import { duckEnvelope, envelopeDbAt, speechSpans, type Breakpoint } from "@/lib/fx/core/duck";
import { toOutput } from "@/lib/fx/core/warp";
import { avDuration } from "@/lib/fx/offline-mix";

export const MUSIC_LANE_HEIGHT = 20;
const SAMPLES = 160;

/** The bed's duck envelope (F, dB), exactly as the preview and the export play it (without file normalisation). */
export function musicEnvelope(doc: EditDocument): Breakpoint[] | null {
  const music = doc.fx?.music;
  if (!music) return null;
  const warp = fxWarp(doc);
  const wordsF: [number, number][] = [];
  for (const word of mappedWords(doc))
    if (Number.isFinite(word.start) && Number.isFinite(word.end) && word.end > word.start)
      wordsF.push([toOutput(warp, word.start), toOutput(warp, word.end)]);
  return duckEnvelope(speechSpans(wordsF), {
    gain_db: music.gain_db, duck_db: music.duck_db, attack: music.attack, release: music.release,
    fade_in: music.fade_in, fade_out: music.fade_out, out_duration: avDuration(warp.outDuration),
  });
}

/**
 * The music lane: the bed's level over the edit timeline, dipping under speech (the duck envelope), drawn as an
 * area. Times are edit seconds like every lane; the level at each is read at its final time.
 */
export function MusicTrack({ doc, className, ...props }: { doc: EditDocument } & ComponentProps<"div">) {
  const t = useTranslations("editor");
  const music = doc.fx?.music;
  const points = musicEnvelope(doc);
  if (!music || !points) return null;
  const total = Math.max(0.01, editDuration(doc));
  const warp = fxWarp(doc);
  const top = music.gain_db, bottom = Math.min(music.gain_db + music.duck_db, music.gain_db) - 12;
  const y = (db: number) => {
    const v = (Math.max(bottom, Math.min(top, db)) - bottom) / Math.max(1e-6, top - bottom);
    return 20 - v * 18;
  };
  const line = Array.from({ length: SAMPLES + 1 }, (_, i) => {
    const e = (i / SAMPLES) * total;
    return `${i},${y(envelopeDbAt(points, toOutput(warp, e))).toFixed(2)}`;
  });
  const duck = t("fxTimeline.duck", { db: Math.round(Math.abs(music.duck_db)) });
  return (
    <div
      role="group"
      aria-label={t("fxTimeline.musicLaneAria")}
      title={`${t(`fx.moods.${music.mood}`)} · ${duck}`}
      className={cn("relative overflow-hidden rounded-md bg-stone-50 dark:bg-stone-900/50", className)}
      style={{ height: MUSIC_LANE_HEIGHT }}
      {...props}
    >
      <svg
        aria-hidden
        className="pointer-events-none absolute inset-0 h-full w-full text-emerald-500/70 dark:text-emerald-400/60"
        viewBox={`0 0 ${SAMPLES} 20`}
        preserveAspectRatio="none"
      >
        <polygon points={`0,20 ${line.join(" ")} ${SAMPLES},20`} fill="currentColor" opacity="0.35" />
        <polyline points={line.join(" ")} fill="none" stroke="currentColor" strokeWidth="1" vectorEffect="non-scaling-stroke" />
      </svg>
      <span className="pointer-events-none relative block truncate px-2 text-[10px] leading-5 font-medium text-stone-600 dark:text-stone-300">
        {t(`fx.moods.${music.mood}`)}
        <span className="text-muted-foreground"> · {duck}</span>
      </span>
    </div>
  );
}
