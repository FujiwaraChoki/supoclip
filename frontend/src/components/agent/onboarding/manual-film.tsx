"use client";

import { useRef } from "react";
import Image from "next/image";
import { useTranslations } from "next-intl";
import { AnimatePresence, motion, useInView } from "motion/react";
import { ArrowUp, Check, Link2, Sparkles, Upload } from "lucide-react";

import { VideoSourceIcon } from "@/components/video-source-icon";
import { WorkflowGlyph } from "@/components/workflow-picker";
import type { Workflow } from "@/lib/workflows";
import { cn } from "@/lib/utils";

import { FilmClip, FilmCursor, between, cameraAt, cursorAt, ease, typed, useCanvasScale, useFilmClock, type Shot } from "./film";

/*
 * "Fill in the details", as a film: a link is pasted, then the camera walks the options row by row
 * (a series, a caption look, a length), Generate, and three real clips come out.
 */

const W = 600;
const H = 470;
const LENGTH = 14.8;
const URL = "youtube.com/watch?v=OpANpfEjNw8";

const T = {
  typeStart: 0.85, typeEnd: 2.1, source: 2.2, panel: 2.75, series: 3.55, caption: 4.7, length: 5.55, generate: 6.15,
  process: 6.45, steps: [6.45, 7.35, 8.25] as const, progressEnd: 9.5, results: 9.7, clips: [9.85, 10.05, 10.25] as const,
  fade: 14.3,
};

const SHOTS: Shot[] = [
  { t: 0, x: 300, y: 150, s: 1.15 },
  { t: 2.3, x: 300, y: 200, s: 1.08, dur: 0.8 },
  { t: 2.95, x: 300, y: 262, s: 1.22, dur: 0.8 },
  { t: 4.05, x: 300, y: 356, s: 1.22, dur: 0.7 },
  { t: 5.0, x: 300, y: 418, s: 1.22, dur: 0.6 },
  { t: 6.3, x: 300, y: 235, s: 1, dur: 0.9 },
  { t: 9.8, x: 300, y: 300, s: 1.06, dur: 1.2 },
  { t: 13.4, x: 300, y: 235, s: 1, dur: 0.8 },
];
const CURSOR_HOME = { x: 500, y: 200 };
const PATH = [
  { start: 0.15, end: 0.75, x: 120, y: 86 },
  { start: 3.0, end: 3.45, x: 210, y: 262 },
  { start: 4.15, end: 4.6, x: 357, y: 357 },
  { start: 5.05, end: 5.45, x: 231, y: 427 },
  { start: 5.65, end: 6.05, x: 464, y: 427 },
  { start: 6.8, end: 7.8, x: 540, y: 330 },
] as const;
const CLICKS = [0.8, T.series, T.caption, T.length, T.generate];

const WORKFLOWS: Workflow[] = ["highlights", "series", "topic", "recap", "trailer"];
const CAPTION_LOOKS = [
  { poster: "/clips/podcast-1.jpg", text: "font-black uppercase text-white [text-shadow:0_2px_0_rgb(0_0_0/0.8)]", accent: "text-yellow-300" },
  { poster: "/clips/articles/price-1.jpg", text: "font-semibold text-white [text-shadow:0_1px_4px_rgb(0_0_0/0.8)]", accent: "underline decoration-2 underline-offset-2" },
  { poster: "/clips/articles/ton-1.jpg", text: "font-black uppercase italic text-white [text-shadow:0_2px_0_rgb(0_0_0/0.8)]", accent: "text-emerald-300" },
  { poster: "/clips/podcast-2.jpg", text: "rounded-md bg-white px-1.5 py-0.5 font-bold text-black", accent: "" },
] as const;
const CLIPS = [
  { src: "/clips/podcast-1.mp4", poster: "/clips/podcast-1.jpg" },
  { src: "/clips/podcast-2.mp4", poster: "/clips/podcast-2.jpg" },
  { src: "/clips/articles/price-2.mp4", poster: "/clips/articles/price-2.jpg" },
];
const SOFT = "shadow-[0_1px_2px_rgb(0_0_0/0.05),0_10px_28px_-14px_rgb(0_0_0/0.25)]";
const scene = { initial: { opacity: 0, y: 14 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0, y: -8, transition: { duration: 0.25 } }, transition: { duration: 0.45, ease: [0.16, 1, 0.3, 1] as const } };

function Label({ children, top }: { children: React.ReactNode; top: number }) {
  return <p className="absolute left-[76px] text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground" style={{ top }}>{children}</p>;
}

export function ManualFilm({ active }: { active: boolean }) {
  const t = useTranslations("agent.onboarding.film");
  const tHome = useTranslations("home");
  const wrapRef = useRef<HTMLDivElement>(null);
  const inView = useInView(wrapRef, { amount: 0.3 });
  const scale = useCanvasScale(wrapRef, W);
  const { t: time } = useFilmClock(LENGTH, inView, T.results + 1);
  const cursor = cursorAt(time, CURSOR_HOME, PATH);
  const pressed = CLICKS.some((click) => time >= click && time < click + 0.16);
  const workflow: Workflow = time >= T.series ? "series" : "highlights";
  const captionLook = time >= T.caption ? 2 : 0;
  const length = time >= T.length ? 2 : 0;
  const processing = time >= T.process && time < T.results;
  const results = time >= T.results;
  const progress = Math.round(3 + 97 * ease(between(time, T.process, T.progressEnd)));
  const step = T.steps.filter((start) => time >= start).length - 1;
  const typingNow = time >= T.typeStart && time < T.source;
  const playing = inView && active;

  return (
    <div ref={wrapRef} className="relative w-full overflow-hidden" style={{ height: H * scale }}>
      <div inert aria-hidden className="absolute left-0 top-0 origin-top-left select-none overflow-hidden" style={{ width: W, height: H, transform: `scale(${scale})` }}>
        <motion.div className="h-full" animate={{ opacity: time < T.fade ? 1 : 0 }} transition={{ duration: 0.5 }}>
          <div className="absolute left-0 top-0 origin-top-left bg-canvas will-change-transform" style={{ width: W, height: H, ...cameraAt(time, SHOTS, W, H) }}>
            <p className="absolute inset-x-0 top-[26px] text-center font-display text-[21px] font-bold tracking-tight">{t("manualTitle")}</p>

            {/* Link field */}
            <div
              className={cn(
                "absolute left-[60px] top-[64px] flex h-[52px] w-[480px] items-center gap-2.5 rounded-[18px] border bg-background px-4 transition-shadow duration-300",
                SOFT,
                typingNow && "border-foreground/25 shadow-[0_0_0_4px_color-mix(in_oklch,var(--brand)_12%,transparent),0_10px_28px_-14px_rgb(0_0_0/0.25)]",
              )}
            >
              {time >= T.source ? <VideoSourceIcon source="youtube" className="size-[18px]" /> : <Link2 className="size-[18px] text-muted-foreground" />}
              <span className={cn("min-w-0 flex-1 truncate text-[14px]", time < T.typeStart && "text-muted-foreground")}>
                {time < T.typeStart ? t("manualPlaceholder") : typed(URL, time, T.typeStart, T.typeEnd)}
                {typingNow && <span className="ml-px inline-block h-4 w-px translate-y-[3px] animate-pulse bg-foreground" />}
              </span>
              <Upload className="size-4 text-muted-foreground" />
              <span className={cn("flex size-8 items-center justify-center rounded-full transition-colors", time >= T.source && time < T.process ? "bg-foreground text-background" : "bg-muted text-muted-foreground")}>
                <ArrowUp className="size-4" />
              </span>
            </div>

            {/* Where links can come from, until one is in */}
            <AnimatePresence>
              {time < T.source && (
                <motion.div key="sources" {...scene} className="absolute inset-x-0 top-[138px] flex flex-col items-center gap-3">
                  <div className="flex items-center">
                    {(["youtube", "twitch", "kick"] as const).map((source, index) => (
                      <span
                        key={source}
                        className={cn("flex size-11 items-center justify-center rounded-[14px] border bg-background shadow-sm", index > 0 && "-ml-2", index === 0 ? "-rotate-6" : index === 2 ? "rotate-6" : "")}
                        style={{ zIndex: 3 - index }}
                      >
                        <VideoSourceIcon source={source} className="size-5" />
                      </span>
                    ))}
                    <span className="-ml-2 flex size-11 rotate-12 items-center justify-center rounded-[14px] bg-foreground text-background shadow-sm"><Upload className="size-4" /></span>
                  </div>
                  <p className="text-[11.5px] text-muted-foreground">{t("sourcesHint")}</p>
                </motion.div>
              )}
            </AnimatePresence>

            {/* The video, once the link is in */}
            <AnimatePresence>
              {time >= T.source && !results && !processing && (
                <motion.div {...scene} transition={{ ...scene.transition, delay: 0.25 }} className="absolute left-[60px] top-[126px] flex w-[480px] items-center gap-3 px-1">
                  <Image src="/clips/source.jpg" alt="" width={72} height={40} className="h-10 w-[72px] rounded-lg object-cover shadow-sm" />
                  <div className="min-w-0">
                    <p className="truncate text-[12.5px] font-medium">{t("manualSource")}</p>
                    <p className="text-[11px] text-muted-foreground">YouTube · 1:04:12</p>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            <AnimatePresence>
              {time >= T.panel && time < T.process && (
                <motion.div key="panel" {...scene} className={cn("absolute left-[60px] top-[184px] h-[272px] w-[480px] rounded-[22px] border bg-background", SOFT)} />
              )}
            </AnimatePresence>
            {time >= T.panel && time < T.process && (
              <motion.div key="panel-content" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.15, duration: 0.35 }}>
                <Label top={200}>{t("whatToMake")}</Label>
                {WORKFLOWS.map((id, index) => (
                  <div
                    key={id}
                    aria-checked={workflow === id}
                    className={cn(
                      "group/workflow absolute top-[218px] flex h-[82px] w-[84px] flex-col items-center overflow-hidden rounded-[14px] border bg-background p-1.5 transition-all duration-300",
                      workflow === id ? "border-foreground ring-1 ring-foreground" : "border-border",
                    )}
                    style={{ left: 76 + index * 92 }}
                  >
                    <WorkflowGlyph id={id} className="h-[50px] w-full [&_svg]:h-[42px]" />
                    <span className="mt-1.5 text-[10.5px] font-medium">{tHome(`workflows.${id}.label`)}</span>
                  </div>
                ))}

                <Label top={314}>{t("captionStyle")}</Label>
                {CAPTION_LOOKS.map((look, index) => (
                  <div
                    key={look.poster}
                    className={cn(
                      "absolute top-[332px] h-[48px] w-[106px] overflow-hidden rounded-[12px] transition-all duration-300",
                      captionLook === index ? "ring-2 ring-foreground ring-offset-2 ring-offset-background" : "ring-1 ring-black/10",
                    )}
                    style={{ left: 76 + index * 114 }}
                  >
                    <Image src={look.poster} alt="" fill sizes="106px" className="scale-110 object-cover blur-[1px] brightness-[0.55]" />
                    <span className="absolute inset-0 flex items-center justify-center text-[11.5px]">
                      <span className={look.text}>{t("captionSample")} <span className={look.accent}>{t("captionAccent")}</span></span>
                    </span>
                  </div>
                ))}

                <Label top={394}>{t("length")}</Label>
                <div className="absolute left-[76px] top-[411px] flex h-8 w-[248px] rounded-full bg-muted p-0.5">
                  {[0, 1, 2, 3].map((index) => (
                    <span key={index} className={cn("relative flex flex-1 items-center justify-center text-[11px] font-medium", length === index ? "text-foreground" : "text-muted-foreground")}>
                      {length === index && <motion.span layoutId="film-length" className="absolute inset-0 rounded-full bg-background shadow-sm" transition={{ type: "spring", stiffness: 380, damping: 30 }} />}
                      <span className="relative">{t(`lengths.${index}`)}</span>
                    </span>
                  ))}
                </div>
                <span
                  className={cn(
                    "absolute left-[404px] top-[407px] flex h-10 w-[120px] items-center justify-center gap-1.5 rounded-full bg-foreground text-[13px] font-medium text-background shadow-md transition-transform duration-150",
                    time >= T.generate && time < T.generate + 0.25 && "scale-95",
                  )}
                >
                  <Sparkles className="size-3.5" />{t("generate")}
                </span>
              </motion.div>
            )}

            {/* Generating */}
            <AnimatePresence>
              {(processing || results) && (
                <motion.div
                  key="progress"
                  {...scene}
                  className={cn("absolute left-[60px] top-[126px] w-[480px] rounded-[20px] border bg-background p-4", SOFT)}
                >
                  <div className="flex items-center gap-3.5">
                    <Image src="/clips/source.jpg" alt="" width={96} height={54} className="h-[54px] w-24 rounded-xl object-cover" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13px] font-semibold">{t("manualSource")}</p>
                      <div className="relative mt-1 h-4 overflow-hidden">
                        <AnimatePresence mode="popLayout" initial={false}>
                          <motion.p
                            key={results ? "ready" : step}
                            initial={{ y: 14, opacity: 0 }}
                            animate={{ y: 0, opacity: 1 }}
                            exit={{ y: -14, opacity: 0 }}
                            transition={{ duration: 0.3 }}
                            className={cn("flex items-center gap-1.5 text-[11.5px]", results ? "font-medium text-emerald-600" : "text-muted-foreground")}
                          >
                            {results && <Check className="size-3" strokeWidth={3} />}
                            {results ? t("ready") : t(`processing.${Math.max(0, step)}`)}
                          </motion.p>
                        </AnimatePresence>
                      </div>
                    </div>
                    <span className="font-display text-[26px] font-bold tabular-nums tracking-tight">{results ? 100 : progress}<span className="text-[14px] text-muted-foreground">%</span></span>
                  </div>
                  <div className="mt-3.5 h-1.5 overflow-hidden rounded-full bg-muted">
                    <div className="h-full rounded-full bg-foreground" style={{ width: `${results ? 100 : progress}%` }} />
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {(processing || results) && (
              <div className="absolute left-[60px] top-[236px] flex w-[480px] justify-center gap-4">
                {CLIPS.map((clip, index) => (
                  <div key={clip.src} className={cn("relative h-[216px] w-[122px] overflow-hidden rounded-[16px] bg-muted", SOFT)}>
                    {time >= T.clips[index] ? (
                      <motion.div className="absolute inset-0" initial={{ opacity: 0, scale: 1.08 }} animate={{ opacity: 1, scale: 1 }} transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}>
                        <FilmClip src={clip.src} poster={clip.poster} playing={playing} className="absolute inset-0 size-full object-cover" />
                        <span className="absolute left-2 top-2 rounded-md bg-black/55 px-1.5 py-0.5 text-[10px] font-semibold text-white backdrop-blur">{t("part", { n: index + 1 })}</span>
                      </motion.div>
                    ) : (
                      <div className="absolute inset-0 overflow-hidden bg-muted">
                        <div className="absolute inset-y-0 -left-full w-full animate-[progress-sheen_1.6s_ease-in-out_infinite] bg-gradient-to-r from-transparent via-background/60 to-transparent" />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            <FilmCursor x={cursor.x} y={cursor.y} pressed={pressed} />
          </div>
        </motion.div>
      </div>
    </div>
  );
}
