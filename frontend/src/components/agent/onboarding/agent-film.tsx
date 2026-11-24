"use client";

import { useRef } from "react";
import { useTranslations } from "next-intl";
import { AnimatePresence, motion, useInView } from "motion/react";
import { ArrowUp, Check, Paperclip } from "lucide-react";

import { AgentMark, type AgentMood } from "@/components/agent/agent-mark";
import { SocialProviderIcon } from "@/components/social-provider-icon";
import type { SocialProviderId } from "@/lib/social";
import { cn } from "@/lib/utils";

import { FilmClip, FilmCursor, between, cameraAt, cursorAt, typed, useCanvasScale, useFilmClock, type Shot } from "./film";

/*
 * "Work with the agent", as a film in four scenes: the request is typed (camera close on the composer),
 * Agent works (one calm card), the clips it picked fan out, and they land on the week's calendar before
 * Agent answers. A scripted camera follows the action.
 */

const W = 600;
const H = 470;
const LENGTH = 15.2;

const T = {
  typeStart: 0.9, typeEnd: 2.7, send: 3.0, user: 3.15,
  work: 3.5, steps: [3.5, 4.4, 5.3] as const, workDone: 6.2,
  fan: 6.45, calendar: 8.8, slots: [9.1, 9.35, 9.6] as const,
  happy: 10.0, replyStart: 10.1, replyEnd: 11.6, chips: 11.8, chipClick: 13.0, fade: 14.6,
};

const SHOTS: Shot[] = [
  { t: 0, x: 300, y: 330, s: 1.1 },
  { t: 3.05, x: 300, y: 235, s: 1, dur: 1.1 },
  { t: 6.35, x: 300, y: 262, s: 1.1, dur: 1 },
  { t: 8.7, x: 300, y: 240, s: 1, dur: 1 },
  { t: 11.6, x: 300, y: 300, s: 1.06, dur: 1 },
  { t: 14.0, x: 300, y: 300, s: 1.1, dur: 1.2 },
];
const CURSOR_HOME = { x: 470, y: 300 };
const PATH = [
  { start: 0.2, end: 0.8, x: 160, y: 426 },
  { start: 2.4, end: 2.9, x: 536, y: 426 },
  { start: 3.5, end: 4.6, x: 520, y: 250 },
  { start: 12.2, end: 12.9, x: 120, y: 364 },
  { start: 13.5, end: 14.4, x: 470, y: 330 },
] as const;
const CLICKS = [0.85, T.send, T.chipClick];

const CLIPS = [
  { src: "/clips/podcast-1.mp4", poster: "/clips/podcast-1.jpg", score: 94, hook: "Publishing every Monday grew listeners 10x" },
  { src: "/clips/articles/price-1.mp4", poster: "/clips/articles/price-1.jpg", score: 89, hook: "What separates artists who improve" },
  { src: "/clips/articles/ton-1.mp4", poster: "/clips/articles/ton-1.jpg", score: 86, hook: "Why an open film needs its community" },
];
const SLOTS: { day: number; provider: SocialProviderId; clip: number }[] = [
  { day: 1, provider: "tiktok", clip: 0 },
  { day: 3, provider: "youtube", clip: 1 },
  { day: 5, provider: "instagram", clip: 2 },
];
const FAN = [
  { x: 96, rotate: -8, y: 14 },
  { x: 200, rotate: 0, y: 0 },
  { x: 304, rotate: 8, y: 14 },
];
const SOFT = "shadow-[0_1px_2px_rgb(0_0_0/0.05),0_10px_28px_-14px_rgb(0_0_0/0.25)]";
const scene = { initial: { opacity: 0, y: 10 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0, y: -6 }, transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] as const } };

export function AgentFilm({ active }: { active: boolean }) {
  const t = useTranslations("agent.onboarding.film");
  const wrapRef = useRef<HTMLDivElement>(null);
  const inView = useInView(wrapRef, { amount: 0.3 });
  const scale = useCanvasScale(wrapRef, W);
  const { t: time } = useFilmClock(LENGTH, inView, T.chips + 1);
  const ask = t("agentAsk");
  const reply = t("agentReply");
  const mood: AgentMood = time >= T.happy && time < T.happy + 1.6 ? "happy" : time >= T.user && time < T.happy ? "thinking" : "idle";
  const cursor = cursorAt(time, CURSOR_HOME, PATH);
  const pressed = CLICKS.some((click) => time >= click && time < click + 0.16);
  const playing = inView && active;
  const step = T.steps.filter((start) => time >= start).length - 1;
  const working = time >= T.work && time < T.fan;
  const workDone = time >= T.workDone;
  const fan = time >= T.fan && time < T.calendar;
  const calendar = time >= T.calendar;
  const typing = time >= T.typeStart && time < T.user;

  return (
    <div ref={wrapRef} className="relative w-full overflow-hidden" style={{ height: H * scale }}>
      <div inert aria-hidden className="absolute left-0 top-0 origin-top-left select-none overflow-hidden" style={{ width: W, height: H, transform: `scale(${scale})` }}>
        <motion.div className="h-full" animate={{ opacity: time < T.fade ? 1 : 0 }} transition={{ duration: 0.5 }}>
          <div className="absolute left-0 top-0 origin-top-left bg-canvas will-change-transform" style={{ width: W, height: H, ...cameraAt(time, SHOTS, W, H) }}>
            {/* Header */}
            <div className="absolute inset-x-0 top-0 flex h-[52px] items-center gap-2.5 border-b bg-background/80 px-5 backdrop-blur">
              <AgentMark mood={mood} className="text-[6.5px]" />
              <span className="text-[14px] font-semibold tracking-tight">Agent</span>
              <span className="ml-auto flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <span className="size-1.5 rounded-full bg-emerald-500" />{t("online")}
              </span>
            </div>

            {/* Before anything is asked: Agent waiting, like the real home screen. */}
            <AnimatePresence>
              {time < T.user && (
                <motion.div key="empty" {...scene} className="absolute inset-x-0 top-[104px] flex flex-col items-center">
                  <AgentMark mood={typing ? "listening" : "idle"} className="text-[15px]" />
                  <p className="mt-4 font-display text-[22px] font-bold tracking-tight">{t("emptyTitle")}</p>
                  <div className="mt-5 flex gap-2">
                    {[0, 1, 2].map((index) => (
                      <span key={index} className="rounded-full border bg-background px-3 py-1.5 text-[11.5px] text-muted-foreground shadow-xs">{t(`emptyChips.${index}`)}</span>
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* The request */}
            <AnimatePresence>
              {time >= T.user && (
                <motion.div {...scene} className="absolute right-6 top-[70px] max-w-[400px] rounded-[18px] rounded-br-md bg-muted px-4 py-2.5 text-[13.5px] leading-snug">
                  {ask}
                </motion.div>
              )}
            </AnimatePresence>

            {/* Agent's side */}
            {time >= T.user && (
              <div className="absolute left-6 top-[124px]">
                <AgentMark mood={mood} className="text-[8px]" />
              </div>
            )}

            <AnimatePresence>
              {working && (
                <motion.div key="work" {...scene} className={cn("absolute left-[72px] top-[124px] w-[400px] rounded-2xl border bg-background p-4", SOFT)}>
                  <div className="flex items-center gap-2.5">
                    <span className="relative flex size-5 items-center justify-center">
                      {workDone ? (
                        <motion.span initial={{ scale: 0 }} animate={{ scale: 1 }} className="flex size-5 items-center justify-center rounded-full bg-foreground text-background">
                          <Check className="size-3" strokeWidth={3} />
                        </motion.span>
                      ) : (
                        <>
                          <span className="absolute inset-0 rounded-full border-2 border-muted" />
                          <span className="absolute inset-0 animate-spin rounded-full border-2 border-transparent border-t-foreground" />
                        </>
                      )}
                    </span>
                    <span className="text-[12px] font-medium text-muted-foreground">{workDone ? t("done") : t("working")}</span>
                  </div>
                  <div className="relative mt-3 h-[22px] overflow-hidden">
                    <AnimatePresence mode="popLayout" initial={false}>
                      <motion.p
                        key={workDone ? "done" : step}
                        initial={{ y: 18, opacity: 0 }}
                        animate={{ y: 0, opacity: 1 }}
                        exit={{ y: -18, opacity: 0 }}
                        transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
                        className={cn("text-[15px] font-semibold tracking-tight", !workDone && "shimmer-text")}
                      >
                        {workDone ? t("ready") : t(`agentSteps.${Math.max(0, step)}`)}
                      </motion.p>
                    </AnimatePresence>
                  </div>
                  <div className="mt-3 grid grid-cols-3 gap-1.5">
                    {T.steps.map((start, index) => {
                      const end = T.steps[index + 1] ?? T.workDone;
                      return (
                        <span key={index} className="h-1 overflow-hidden rounded-full bg-muted">
                          <span className="block h-full rounded-full bg-foreground" style={{ width: `${Math.round(between(time, start, end) * 100)}%` }} />
                        </span>
                      );
                    })}
                  </div>
                  <div className="mt-3 flex h-6 gap-1.5">
                    {T.steps.map((start, index) => {
                      const end = T.steps[index + 1] ?? T.workDone;
                      if (time < end) return null;
                      return (
                        <motion.span
                          key={index}
                          initial={{ opacity: 0, scale: 0.85 }}
                          animate={{ opacity: 1, scale: 1 }}
                          transition={{ type: "spring", stiffness: 420, damping: 22 }}
                          className="flex items-center gap-1 rounded-full bg-muted px-2 text-[11px] font-medium"
                        >
                          <Check className="size-3 text-emerald-600" strokeWidth={3} />{t(`agentChips.${index}`)}
                        </motion.span>
                      );
                    })}
                  </div>
                </motion.div>
              )}

              {fan && (
                <motion.div key="fan" {...scene} className="absolute left-[72px] top-[118px] h-[270px] w-[460px]">
                  {CLIPS.map((clip, index) => (
                    <motion.div
                      key={clip.src}
                      initial={{ opacity: 0, y: 40, rotate: 0, x: 200 }}
                      animate={{ opacity: 1, y: FAN[index].y, rotate: FAN[index].rotate, x: FAN[index].x - 72 }}
                      transition={{ delay: index * 0.12, type: "spring", stiffness: 170, damping: 19 }}
                      className={cn("absolute top-0 h-[236px] w-[134px] overflow-hidden rounded-[18px] bg-stone-900 ring-1 ring-black/10", SOFT, index === 1 && "z-10")}
                    >
                      <FilmClip src={clip.src} poster={clip.poster} playing={playing} className="absolute inset-0 size-full object-cover" />
                      <span className="absolute left-2 top-2 rounded-md bg-black/55 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-white backdrop-blur">{clip.score}</span>
                      <span className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 via-black/40 to-transparent p-2.5 pt-8 text-[10.5px] font-semibold leading-snug text-white">{clip.hook}</span>
                    </motion.div>
                  ))}
                </motion.div>
              )}

              {calendar && (
                <motion.div key="calendar" {...scene} className={cn("absolute left-[72px] top-[124px] w-[504px] rounded-2xl border bg-background p-3.5", SOFT)}>
                  <div className="mb-2.5 flex items-center justify-between">
                    <span className="text-[12.5px] font-semibold">{t("thisWeek")}</span>
                    <span className="flex items-center gap-1 text-[11px] text-muted-foreground"><Check className="size-3 text-emerald-600" strokeWidth={3} />{t("scheduled")}</span>
                  </div>
                  <div className="grid grid-cols-7 gap-1.5">
                    {Array.from({ length: 7 }, (_, day) => {
                      const slot = SLOTS.find((s) => s.day === day);
                      const shown = slot && time >= T.slots[SLOTS.indexOf(slot)];
                      return (
                        <div key={day} className={cn("flex h-[112px] flex-col items-center rounded-xl pt-1.5", slot ? "bg-muted/70" : "bg-muted/30")}>
                          <span className="text-[10px] font-medium text-muted-foreground">{t(`days.${day}`)}</span>
                          <AnimatePresence>
                            {shown && (
                              <motion.span
                                initial={{ opacity: 0, y: -24, scale: 0.6 }}
                                animate={{ opacity: 1, y: 0, scale: 1 }}
                                transition={{ type: "spring", stiffness: 300, damping: 18 }}
                                className="relative mt-1.5 block h-[68px] w-[40px] overflow-hidden rounded-lg bg-stone-900 shadow-md"
                              >
                                {/* eslint-disable-next-line @next/next/no-img-element */}
                                <img src={CLIPS[slot.clip].poster} alt="" className="size-full object-cover" />
                                <span className="absolute -bottom-0 left-1/2 flex size-4 -translate-x-1/2 translate-y-0 items-center justify-center rounded-full bg-background text-foreground shadow ring-1 ring-black/5">
                                  <SocialProviderIcon provider={slot.provider} className="size-2.5" />
                                </span>
                              </motion.span>
                            )}
                          </AnimatePresence>
                          {shown && <span className="mt-1 text-[9.5px] font-medium tabular-nums">7:00</span>}
                        </div>
                      );
                    })}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {time >= T.replyStart && (
              <p className="absolute left-[72px] top-[300px] w-[480px] text-[13.5px] leading-relaxed">{typed(reply, time, T.replyStart, T.replyEnd)}</p>
            )}
            <AnimatePresence>
              {time >= T.chips && (
                <motion.div {...scene} className="absolute left-[72px] top-[352px] flex gap-2">
                  {[t("chips.0"), t("chips.1")].map((chip, index) => (
                    <span
                      key={chip}
                      className={cn(
                        "flex h-7 items-center rounded-full border bg-background px-3 text-[11.5px] font-medium shadow-xs transition-colors duration-200",
                        index === 0 && time >= T.chipClick && "border-foreground bg-foreground text-background",
                      )}
                    >
                      {chip}
                    </span>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>

            {/* Composer */}
            <div className={cn("absolute inset-x-10 bottom-[18px] flex h-[52px] items-center gap-2.5 rounded-[20px] border bg-background px-4 transition-shadow duration-300", SOFT, typing && "border-foreground/25 shadow-[0_0_0_4px_color-mix(in_oklch,var(--brand)_12%,transparent),0_10px_28px_-14px_rgb(0_0_0/0.25)]")}>
              <Paperclip className="size-4 text-muted-foreground" />
              <span className={cn("min-w-0 flex-1 truncate text-[13.5px]", !typing && "text-muted-foreground")}>
                {typing ? (
                  <>{typed(ask, time, T.typeStart, T.typeEnd)}<span className="ml-px inline-block h-4 w-px translate-y-[3px] animate-pulse bg-foreground" /></>
                ) : t("placeholder")}
              </span>
              <span className={cn("flex size-8 items-center justify-center rounded-full transition-colors", time >= T.typeEnd - 0.3 && time < T.user ? "bg-foreground text-background" : "bg-muted text-muted-foreground")}>
                <ArrowUp className="size-4" />
              </span>
            </div>

            <FilmCursor x={cursor.x} y={cursor.y} pressed={pressed} />
          </div>
        </motion.div>
      </div>
    </div>
  );
}
