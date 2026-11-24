"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { motion, useInView } from "motion/react";
import { ArrowRight, Link2 } from "lucide-react";

import { AgentMark } from "@/components/agent/agent-mark";
import { Button } from "@/components/ui/button";
import { VideoSourceIcon } from "@/components/video-source-icon";
import { WorkflowGlyph } from "@/components/workflow-picker";
import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";
import type { Workflow } from "@/lib/workflows";
import { cn } from "@/lib/utils";

import { FilmClip } from "./film";

const CYCLE: Workflow[] = ["highlights", "series", "topic", "recap", "trailer"];

/** Something that changes every `ms` while on screen (and holds still for reduced motion). */
function useTicker(count: number, ms: number) {
  const reduced = usePrefersReducedMotion();
  const [index, setIndex] = useState(0);
  useEffect(() => {
    if (reduced) return;
    const timer = setInterval(() => setIndex((i) => (i + 1) % count), ms);
    return () => clearInterval(timer);
  }, [count, ms, reduced]);
  return index;
}

function PasteIllustration() {
  const t = useTranslations("agent.onboarding.film");
  const tick = useTicker(2, 1800);
  return (
    <div className="flex h-full flex-col justify-center gap-2 px-4">
      <div className="flex items-center gap-2 rounded-xl border bg-background px-3 py-2.5 shadow-xs">
        {tick ? <VideoSourceIcon source="youtube" className="size-4" /> : <Link2 className="size-4 text-muted-foreground" />}
        <span className={cn("truncate text-xs transition-colors", !tick && "text-muted-foreground")}>{tick ? "youtube.com/watch?v=…" : t("manualPlaceholder")}</span>
      </div>
      <motion.div animate={{ opacity: tick ? 1 : 0, y: tick ? 0 : -4 }} className="flex items-center gap-2 px-1 text-[11px] text-muted-foreground">
        <span className="h-4 w-7 rounded bg-[url('/clips/source.jpg')] bg-cover" />
        <span className="truncate">{t("manualSource")}</span>
      </motion.div>
    </div>
  );
}

function PickIllustration() {
  const tHome = useTranslations("home");
  const index = useTicker(CYCLE.length, 1400);
  const workflow = CYCLE[index];
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2">
      <motion.div key={workflow} initial={{ opacity: 0, scale: 0.94 }} animate={{ opacity: 1, scale: 1 }} transition={{ duration: 0.3 }} className="group/workflow w-40" aria-checked>
        <WorkflowGlyph id={workflow} />
      </motion.div>
      <span className="text-xs font-medium">{tHome(`workflows.${workflow}.label`)}</span>
    </div>
  );
}

function ClipsIllustration() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { amount: 0.4 });
  return (
    <div ref={ref} className="flex h-full items-center justify-center gap-2">
      {["/clips/podcast-1", "/clips/articles/ton-2", "/clips/podcast-2"].map((clip, index) => (
        <div key={clip} className={cn("relative aspect-[9/16] w-[3.6rem] overflow-hidden rounded-lg bg-stone-900 shadow-md", index === 1 && "w-[4.2rem]")}>
          <FilmClip src={`${clip}.mp4`} poster={`${clip}.jpg`} playing={inView} className="absolute inset-0 size-full object-cover" />
        </div>
      ))}
    </div>
  );
}

const STEPS = [
  { key: "paste", Illustration: PasteIllustration },
  { key: "pick", Illustration: PickIllustration },
  { key: "clips", Illustration: ClipsIllustration },
] as const;

/** After choosing the create form: how it works in three steps, then straight to it. */
export function ManualWelcome({ onFinish }: { onFinish: () => void }) {
  const t = useTranslations("agent.onboarding.manual");
  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col items-center px-4 pb-20 pt-12 sm:px-8 md:pt-[11vh]">
      <motion.h1
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
        className="text-center font-display text-3xl font-bold tracking-tight sm:text-5xl"
      >
        {t("title")}
      </motion.h1>
      <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.15 }} className="mt-3 max-w-md text-center text-muted-foreground">
        {t("subtitle")}
      </motion.p>

      <div className="mt-12 grid w-full gap-4 sm:grid-cols-3">
        {STEPS.map(({ key, Illustration }, index) => (
          <motion.div
            key={key}
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 + index * 0.12, duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden rounded-3xl border bg-card shadow-[0_20px_50px_-30px_rgb(28_25_23/0.35)]"
          >
            <div className="h-44 border-b bg-[radial-gradient(120%_90%_at_50%_0%,var(--muted),transparent)]">
              <Illustration />
            </div>
            <div className="p-5">
              <p className="font-mono text-xs text-muted-foreground">0{index + 1}</p>
              <p className="mt-1 font-semibold tracking-tight">{t(`steps.${key}.title`)}</p>
              <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{t(`steps.${key}.body`)}</p>
            </div>
          </motion.div>
        ))}
      </div>

      <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.65 }} className="mt-10 flex flex-col items-center gap-4">
        <Button size="lg" onClick={onFinish} className="h-12 rounded-full px-7 text-base">
          {t("cta")}<ArrowRight className="size-4" />
        </Button>
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <AgentMark className="text-[5px]" />{t("agentNearby")}
        </p>
      </motion.div>
    </div>
  );
}
